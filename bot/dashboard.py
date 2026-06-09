"""Static HTML dashboard generator for the analysis archive.

Reads ``~/.tradingagents/archive/YYYY-MM-DD/*.json`` and emits:

  ``~/.tradingagents/archive/index.html``        — date-grouped catalog
  ``~/.tradingagents/archive/<DATE>/<T>.html``   — per-analysis detail page

Phase 2 of the dashboard rollout. The pages are plain HTML with inline
CSS — no CDN, no JS framework — so Phase 3 (systemd hosting) can serve
them straight from any static file server, and Phase 4 can layer
JS-side search/filter on top without restructuring the file tree.

Called from ``bot.analyzer`` after each successful archive write. All
errors are caught and logged; dashboard generation never breaks the
analysis pipeline.
"""

from __future__ import annotations

import datetime
import html as _html
import json
import logging
import os
import re
from collections import Counter
from pathlib import Path

from bot.archive import ARCHIVE_ROOT

log = logging.getLogger(__name__)

# ─── extraction helpers ──────────────────────────────────────────────
# The summary string format is owned by bot.analyzer._format_summary;
# these patterns mirror that output.
_RATING_RE = re.compile(r"🎯 최종 판정:\s*\*\*([^*]+?)\*\*")
_STANCE_LINE_RE = re.compile(r"^(?:📈|💬|📰|💰)[^\n]*·[^\n]*$", re.MULTILINE)
_PAST_OUTCOMES_RE = re.compile(r"^(📒\s*지난 추천[^\n]+)$", re.MULTILINE)


def _extract(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text or "")
    return m.group(1).strip() if (m and m.lastindex) else (m.group(0).strip() if m else "")


# ─── stats sources ───────────────────────────────────────────────────
_USAGE_LOG_PATH = Path.home() / ".tradingagents" / "usage.jsonl"
_MEMORY_LOG_PATH = Path.home() / ".tradingagents" / "memory" / "trading_memory.md"
_KRW_PER_USD = 1380  # mirrors usage_tracker's constant; keep in sync

_BATCH_REGEN = False  # True during regenerate_index — skip live network fetches

# ─── issue detection ─────────────────────────────────────────────────
# Patterns that indicate something went wrong inside an otherwise-
# completed analysis. Hard failures (timeout / process exit) are tracked
# separately via usage_tracker.log_failure → usage.jsonl type:'failure'.
_FAILURE_PLACEHOLDER = (
    "_(이번 분석은 모델 응답 오류로 미완성. 다른 티커로 재시도해보세요.)_"
)
_SECTION_HEADER_RE = re.compile(r"^##\s+([^\n]+)$", re.MULTILINE)
# Markdown header (## / ### / #### ...) for section-label tracking in the
# index-page snippet search. _SECTION_HEADER_RE itself is `##`-only because
# the issues detector needs the analyst-level grouping; the index search
# walks h3+ too so '### 결론' style sub-headers also attach context.
_ANY_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _index_lines_from_full_report(
    full: str, max_lines: int = 200, max_line_chars: int = 200
) -> list[dict]:
    """Walk a saved analysis's full_report markdown, return a compact
    line index for the index-page snippet search. Each entry =
    ``{"sec": <last seen section header>, "txt": <line, stripped + capped>}``.

    Empty lines + header lines themselves are excluded — the section
    header rides along as ``sec`` on subsequent body lines. Lines shorter
    than 3 chars are dropped (table separators, bullet markers alone).

    When total exceeds ``max_lines`` we take front half + back half so
    BOTH the market-analyst block (early in the report) and the
    Portfolio Manager verdict (late) stay searchable. The middle gets
    dropped silently — the search hit rate on Trader / Risk debate text
    is rarely the deciding factor, while a missing PM verdict makes the
    search look broken to the user.
    """
    lines: list[dict] = []
    current_section = ""
    for raw in (full or "").splitlines():
        s = raw.strip()
        m = _ANY_HEADER_RE.match(s)
        if m:
            # Strip leading bullet/marker noise + cap at 60 so very long
            # h3 lines don't bloat the JSON attribute.
            current_section = m.group(2).strip()[:60]
            continue
        if len(s) < 3:
            continue
        lines.append({"sec": current_section, "txt": s[:max_line_chars]})
    if len(lines) > max_lines:
        front = max_lines // 2
        back = max_lines - front
        lines = lines[:front] + lines[-back:]
    return lines
_ISSUE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"리스크 지표 계산 실패"),
     "리스크 지표 계산 실패 (도구)"),
    (re.compile(r"거시 지표 데이터를 가져오지 못했습니다"),
     "거시 지표 fetch 실패 (도구)"),
    (re.compile(r"`?get_risk_metrics`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음|not available|unavailable)",
                re.DOTALL),
     "리스크 지표 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_macro_context`?.*?(?:오류|실패|N/A|사용할 수 없|데이터 없음|not available|unavailable)",
                re.DOTALL),
     "거시 배경 도구 사용 안 됨 (모델)"),
    (re.compile(r"`?get_sector_relative_strength`?.*?(?:오류|실패|사용할 수 없|not available|unavailable)",
                re.DOTALL),
     "섹터 상대 강도 도구 사용 안 됨 (모델)"),
    (re.compile(r"매크로 컨텍스트.*?(?:오류|도구 오류)"),
     "매크로 컨텍스트 오류 (모델)"),
    # Generic tool-apology phrases anywhere in the body — catches the
    # GOOGL 2026-05-10 case where the market analyst opened in English
    # ("tools are not available") and the news analyst buried "죄송합니다.
    # 도구를 사용할 수 없" deep in the body. These bypass the older
    # tool-name-anchored patterns above.
    (re.compile(r"tools? (?:are|is) (?:not available|unavailable)", re.IGNORECASE),
     "도구 사용 불가 사과 (영어)"),
    (re.compile(r"Therefore,?\s*I cannot (?:provide|generate)", re.IGNORECASE),
     "도구 사용 불가 사과 (영어)"),
    (re.compile(r"도구를 사용할 수 없|도구가 유효하지 않"),
     "도구 사용 불가 사과 (한국어)"),
    (re.compile(r"API 호출 실패"),
     "API 호출 실패 사과 (모델)"),
]


# Per-report section markers — should appear EXACTLY once in a finished
# report. When the fundamentals analyst goes into a repetition loop (the
# ONTO 2026-05-10 case) it sometimes emits the trailing valuation /
# decision blocks twice, and _polish's drop-repeated pass either misses
# the duplication pattern or hits the SIGALRM step guard. The dashboard
# audits the assembled body separately so duplications still surface.
_DUP_SECTION_MARKERS = (
    "🧭 투자 계획",
    "💼 트레이더 제안",
    "✅ 최종 결정",
)


def _detect_issues(record: dict) -> list[str]:
    """Walk a single archived analysis and return a list of human-readable
    issue strings. Empty list means clean run."""
    issues: list[str] = []
    full = record.get("full_report", "") or ""
    summary = record.get("summary", "") or ""

    # Map each FAILURE_PLACEHOLDER occurrence in the body to whichever
    # '## …' section header most recently preceded it.
    headers = list(_SECTION_HEADER_RE.finditer(full))
    for ph in re.finditer(re.escape(_FAILURE_PLACEHOLDER), full):
        # Find most recent header before this placeholder.
        section = None
        for h in headers:
            if h.start() < ph.start():
                section = h.group(1).strip()
            else:
                break
        label = f"{section} 섹션 미완성" if section else "섹션 미완성"
        if label not in issues:
            issues.append(label)

    # Generic placeholder in summary too (rare — usually summary skips it)
    if _FAILURE_PLACEHOLDER in summary and not any(
        "미완성" in i for i in issues
    ):
        issues.append("요약 미완성")

    # Tool-related issues across the body
    seen_msgs: set[str] = set()
    for pat, msg in _ISSUE_PATTERNS:
        if pat.search(full) and msg not in seen_msgs:
            seen_msgs.add(msg)
            issues.append(msg)

    # Section duplication: each of the 투자 계획 / 트레이더 / 최종 결정
    # markers should appear once. If any appears twice or more, an
    # analyst (most often fundamentals) emitted its valuation / decision
    # block in a repetition loop and _polish didn't deduplicate. Surface
    # as a dashboard issue so the user notices the report quality dip.
    dup_labels = [
        marker for marker in _DUP_SECTION_MARKERS
        if full.count(marker) > 1
    ]
    if dup_labels:
        issues.append(
            "섹션 중복 — " + " · ".join(dup_labels)
            + " (분석가 반복 루프, polish 미정리)"
        )
    return issues


def _read_hard_failures(window_days: int = 365) -> list[dict]:
    """Read type:'failure' records from usage.jsonl. These are analyses
    that didn't complete (timeout / exception)."""
    if not _USAGE_LOG_PATH.exists():
        return []
    cutoff = _now_ts() - window_days * 86400
    out: list[dict] = []
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "failure" and rec.get("ts", 0) >= cutoff:
                    out.append(rec)
    except Exception as exc:
        log.warning("dashboard: hard-failure read failed: %s", exc)
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return out


def _read_tool_failures(window_hours: int = 24) -> dict[str, int]:
    """Aggregate type:'tool_failure' records over the recent window. Returns
    {tool_name: count}, ordered by count descending. Used for the
    dashboard's tool-health card so a yfinance / alpha vantage outage
    becomes visible before it cascades into analyst failures."""
    if not _USAGE_LOG_PATH.exists():
        return {}
    cutoff = _now_ts() - window_hours * 3600
    counts: dict[str, int] = {}
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "tool_failure":
                    continue
                if rec.get("ts", 0) < cutoff:
                    continue
                tool = rec.get("tool") or "unknown"
                counts[tool] = counts.get(tool, 0) + 1
    except Exception as exc:
        log.warning("dashboard: tool_failure read failed: %s", exc)
    return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))


def _now_ts() -> float:
    import time
    return time.time()


def _read_usage_records() -> list[dict]:
    """Read raw llm_call records from usage.jsonl. Read-only — does NOT
    rotate the file (that's owned by usage_tracker)."""
    if not _USAGE_LOG_PATH.exists():
        return []
    out: list[dict] = []
    try:
        with open(_USAGE_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("dashboard: usage.jsonl read failed: %s", exc)
    return out


def _read_memory_resolved() -> list[dict]:
    """Read trading_memory.md and return only resolved entries (those
    with realized return data). Pending entries get skipped."""
    info = _read_memory_full()
    return info["resolved"]


def _read_memory_full() -> dict:
    """Single pass over trading_memory.md returning both the resolved
    entries (for accuracy calc) and the pending bucket counts (so the
    UI can be transparent about why accuracy might be based on a tiny
    sample). The dashboard's accuracy card stops being misleading when
    we surface 'evaluated 1 / pending 32 / hold 12' instead of just '1/1'.
    """
    out = {
        "resolved": [],          # list of {date, ticker, rating, raw, alpha}
        "pending_directional": 0,  # pending Buy/Sell/Over/Underweight
        "pending_hold": 0,         # pending Hold (won't ever count in accuracy)
        "resolved_hold": 0,        # resolved Hold (excluded from accuracy denom)
    }
    if not _MEMORY_LOG_PATH.exists():
        return out
    try:
        text = _MEMORY_LOG_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        log.warning("dashboard: memory log read failed: %s", exc)
        return out
    blocks = text.split("\n\n<!-- ENTRY_END -->\n\n")
    # Match the format used by memory.py — line-by-line within the
    # OUTCOMES section of each block, capturing 15d / 30d follow-ups.
    outcome_line_re = re.compile(
        r"^\s*(\d+)d\s*\|\s*([+-]?\d+\.?\d*%)\s*\|\s*([+-]?\d+\.?\d*%p?)\s*$",
        re.MULTILINE,
    )
    seen: set[tuple[str, str]] = set()  # de-dupe by (date, ticker)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        tag = lines[0].strip()
        if not (tag.startswith("[") and tag.endswith("]")):
            continue
        fields = [f.strip() for f in tag[1:-1].split("|")]
        if len(fields) < 4:
            continue
        date, ticker, rating = fields[0], fields[1], fields[2]
        rating_l = rating.lower()
        directional = rating_l in ("buy", "overweight", "sell", "underweight")
        is_hold = rating_l == "hold"
        is_pending = (fields[3] == "pending") or len(fields) < 6

        key = (date, ticker)
        if key in seen:
            continue
        seen.add(key)

        if is_pending:
            if directional:
                out["pending_directional"] += 1
            elif is_hold:
                out["pending_hold"] += 1
        else:
            if is_hold:
                out["resolved_hold"] += 1
            # Parse long-horizon outcomes from the body's OUTCOMES section.
            # Iterating finditer over the joined body keeps the scan O(n)
            # per block; misformatted lines are silently dropped.
            body = "\n".join(lines[1:])
            outcomes_extra: list[dict] = []
            if "OUTCOMES:" in body:
                # Restrict the scan to the OUTCOMES sub-block so we don't
                # accidentally match e.g. '15d back-test' in REFLECTION prose.
                outcomes_idx = body.find("OUTCOMES:")
                outcomes_body = body[outcomes_idx:]
                for m in outcome_line_re.finditer(outcomes_body):
                    outcomes_extra.append({
                        "days": int(m.group(1)),
                        "raw": m.group(2),
                        "alpha": m.group(3),
                    })
                outcomes_extra.sort(key=lambda o: o["days"])
            out["resolved"].append({
                "date": date, "ticker": ticker, "rating": rating,
                "raw": fields[3], "alpha": fields[4],
                "outcomes_extra": outcomes_extra,
            })
    return out


def _parse_pct(s: str) -> float | None:
    """Parse '+5.0%' / '-3.2%' / 'n/a' → float (5.0 / -3.2 / None).
    Returns None for NaN/inf — yfinance occasionally produces NaN Close
    values for non-US ETFs, which propagates as '+nan%' into the log."""
    import math
    if not s or s == "n/a":
        return None
    try:
        v = float(s.rstrip("%").replace("+", ""))
        return None if (math.isnan(v) or math.isinf(v)) else v
    except ValueError:
        return None


def _compute_stats(records: list[dict]) -> dict:
    """Roll up archive + usage + memory into the headline numbers shown
    on the dashboard's stats panel."""
    # ── analysis counts and timing ──
    total = len(records)
    dates = [r.get("trade_date") for r in records if r.get("trade_date")]
    first_date = min(dates) if dates else None
    last_date = max(dates) if dates else None
    span_days = 0
    if first_date and last_date:
        try:
            d0 = datetime.date.fromisoformat(first_date)
            d1 = datetime.date.fromisoformat(last_date)
            span_days = (d1 - d0).days + 1
        except Exception:
            pass
    elapsed_vals = [
        float(r.get("elapsed_sec") or 0)
        for r in records
        if r.get("elapsed_sec")
    ]
    avg_elapsed = sum(elapsed_vals) / len(elapsed_vals) if elapsed_vals else 0.0
    ticker_counter = Counter(
        r["ticker"] for r in records if r.get("ticker")
    )
    top_ticker, top_count = (
        ticker_counter.most_common(1)[0] if ticker_counter else ("-", 0)
    )

    # ── cost: today (KST) and current month (KST) ──
    # Two windows the user actually thinks in. The KST date of each
    # record is computed once and reused for both buckets.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now_kst = datetime.datetime.now(kst)
    today_str = now_kst.strftime("%Y-%m-%d")
    month_prefix = now_kst.strftime("%Y-%m")

    usage = _read_usage_records()
    today_cost_usd = 0.0
    month_cost_usd = 0.0
    month_cost_by_model: dict[str, float] = {}
    # Per-subsystem breakdown (분석 / Screener / SV) so the main dashboard
    # surfaces where the total bill is coming from. Screener Pro calls
    # land in usage.jsonl with subsystem='screener'; SV calls live in
    # ~/standardview/sv_usage.jsonl (separate file, KST-date tagged).
    _sub_keys = {"분석": 0.0, "Screener": 0.0, "Daily Byte": 0.0,
                 "청약": 0.0, "부동산": 0.0, "블로그": 0.0, "SV": 0.0,
                 "수출입": 0.0}
    today_cost_by_sub_usd: dict[str, float] = dict(_sub_keys)
    month_cost_by_sub_usd: dict[str, float] = dict(_sub_keys)
    for r in usage:
        if r.get("type") != "llm_call":
            continue
        ts = r.get("ts")
        if not ts:
            continue
        rec_day = datetime.datetime.fromtimestamp(ts, kst).strftime("%Y-%m-%d")
        cost = r.get("cost_usd", 0) or 0
        _subsys = r.get("subsystem")
        sub = ({"screener": "Screener", "daily_byte": "Daily Byte",
                "cheongyak": "청약", "realestate": "부동산",
                "blog": "블로그"}.get(_subsys, "분석"))
        if rec_day.startswith(month_prefix):
            month_cost_usd += cost
            m = r.get("model") or "unknown"
            month_cost_by_model[m] = month_cost_by_model.get(m, 0.0) + cost
            month_cost_by_sub_usd[sub] += cost
            if rec_day == today_str:
                today_cost_usd += cost
                today_cost_by_sub_usd[sub] += cost

    # Standard View cost — read ~/standardview/sv_usage.jsonl which stores
    # cost_krw directly (KST date pre-tagged). Convert KRW → USD via the
    # same 1330 rate the dashboard uses for KRW display. Failure here is
    # silent — SV may be running on a separate host without local file.
    _sv_usage_path = Path.home() / "standardview" / "sv_usage.jsonl"
    if _sv_usage_path.exists():
        try:
            import json as _j
            with open(_sv_usage_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = _j.loads(line)
                    except Exception:
                        continue
                    cost_krw_sv = rec.get("cost_krw", 0) or 0
                    if cost_krw_sv <= 0:
                        continue
                    cost_usd_sv = cost_krw_sv / 1330.0
                    rec_day_sv = rec.get("date") or ""
                    if rec_day_sv.startswith(month_prefix):
                        month_cost_usd += cost_usd_sv
                        month_cost_by_sub_usd["SV"] += cost_usd_sv
                        # Roll SV into the gemini-2.5-flash bucket so the
                        # by-model breakdown stays accurate (SV uses flash).
                        month_cost_by_model["gemini-2.5-flash"] = (
                            month_cost_by_model.get("gemini-2.5-flash", 0.0)
                            + cost_usd_sv
                        )
                        if rec_day_sv == today_str:
                            today_cost_usd += cost_usd_sv
                            today_cost_by_sub_usd["SV"] += cost_usd_sv
        except Exception as exc:
            log.warning("dashboard: SV usage read failed: %s", exc)

    # 한국 수출입(trade) cost — 별도 repo(stock-trade)가 남기는 usage.jsonl
    # 을 읽어 메인 합산에 포함 (사용자 정책 2026-06-02 — nav 에 링크된 비용
    # -발생 surface 는 메인 대시보드 총합에 합산). 경로: $TRADE_DATA_DIR/
    # usage.jsonl, 미설정 시 ~/.trade/usage.jsonl. SV 와 달리 그 repo 의
    # 스키마를 우리가 100% 통제하지 못하므로 방어적으로 cost_usd / cost_krw
    # 양쪽 + ts(epoch) / date(YYYY-MM-DD str) 양쪽 tolerant. 파일 부재·다른
    # 호스트 시 silent skip. 모델 분포는 미상이라 by_model 에 미합산(총합·
    # subsystem 분포만 — SV 가 flash 로 roll 하는 것과 달리 trade 모델 불명).
    _trade_dir = os.environ.get("TRADE_DATA_DIR", "").strip()
    _trade_usage_path = (
        Path(_trade_dir) / "usage.jsonl" if _trade_dir
        else Path.home() / ".trade" / "usage.jsonl"
    )
    if _trade_usage_path.exists():
        try:
            import json as _jt
            with open(_trade_usage_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = _jt.loads(line)
                    except Exception:
                        continue
                    # cost: USD 우선, 없으면 KRW→USD 환산.
                    _cu = rec.get("cost_usd")
                    if _cu is not None and _cu > 0:
                        cost_usd_tr = float(_cu)
                    else:
                        _ck = rec.get("cost_krw", 0) or 0
                        cost_usd_tr = float(_ck) / 1330.0 if _ck > 0 else 0.0
                    if cost_usd_tr <= 0:
                        continue
                    # date: 'date'(YYYY-MM-DD str) 우선, 없으면 ts(epoch)→KST.
                    _rd = rec.get("date")
                    if isinstance(_rd, str) and _rd:
                        rec_day_tr = _rd
                    else:
                        _ts = rec.get("ts")
                        if not _ts:
                            continue
                        try:
                            rec_day_tr = datetime.datetime.fromtimestamp(
                                float(_ts), kst).strftime("%Y-%m-%d")
                        except Exception:
                            continue
                    if rec_day_tr.startswith(month_prefix):
                        month_cost_usd += cost_usd_tr
                        month_cost_by_sub_usd["수출입"] += cost_usd_tr
                        if rec_day_tr == today_str:
                            today_cost_usd += cost_usd_tr
                            today_cost_by_sub_usd["수출입"] += cost_usd_tr
        except Exception as exc:
            log.warning("dashboard: trade usage read failed: %s", exc)

    # ── recommendation accuracy from memory log ──
    # Accuracy criterion: ALPHA (raw − sector ETF benchmark), not raw.
    # Two reasons:
    #  (1) Consistency with the per-card outcome display (_render_outcome_html
    #      below) which has been alpha-based all along. The two surfaces
    #      disagreed under the previous raw-based logic — same recommendation
    #      could show '✓' on its card while counting as 'miss' in the headline
    #      accuracy denominator (e.g. Buy + raw +5% / bench SPY +8% → alpha
    #      −3%p → card miss, stat correct).
    #  (2) Alpha is the more meaningful "did the bot pick well?" signal.
    #      Raw-only would mark every Buy 'correct' in a +10% market regardless
    #      of stock-picking skill; alpha controls for the sector tide.
    mem = _read_memory_full()
    resolved = mem["resolved"]
    correct = 0
    evaluated = 0
    returns: list[float] = []
    alphas: list[float] = []
    for e in resolved:
        ret = _parse_pct(e.get("raw", ""))
        alpha = _parse_pct(e.get("alpha", ""))
        if ret is not None:
            returns.append(ret)
        if alpha is not None:
            alphas.append(alpha)
        rating = (e.get("rating") or "").lower()
        # Alpha is the accuracy criterion. If alpha couldn't be computed
        # (benchmark fetch failed), the entry doesn't count toward accuracy —
        # same handling we already use for raw when raw is None.
        if alpha is None:
            continue
        if rating in ("buy", "overweight"):
            evaluated += 1
            if alpha > 0:
                correct += 1
        elif rating in ("sell", "underweight"):
            evaluated += 1
            if alpha < 0:
                correct += 1
        # 'hold' is excluded from the accuracy denominator (no clean
        # directional bet) but still rolls into the avg_return / avg_alpha
        # totals — the headline numbers cover Hold + directional picks alike.
    accuracy = correct / evaluated if evaluated else None
    avg_return = sum(returns) / len(returns) if returns else None
    avg_alpha = sum(alphas) / len(alphas) if alphas else None

    return {
        "total": total,
        "first_date": first_date,
        "last_date": last_date,
        "span_days": span_days,
        "avg_elapsed_sec": avg_elapsed,
        "top_ticker": top_ticker,
        "top_count": top_count,
        "ticker_distinct": len(ticker_counter),
        "today_cost_usd": today_cost_usd,
        "month_cost_usd": month_cost_usd,
        "month_cost_by_model": month_cost_by_model,
        "today_cost_by_sub_usd": today_cost_by_sub_usd,
        "month_cost_by_sub_usd": month_cost_by_sub_usd,
        "today_label": now_kst.strftime("%-m월 %-d일"),
        "month_label": now_kst.strftime("%Y년 %-m월"),
        "tool_failures_24h": _read_tool_failures(window_hours=24),
        "evaluated": evaluated,
        "correct": correct,
        "accuracy": accuracy,
        "avg_return": avg_return,
        "avg_alpha": avg_alpha,
        "resolved_count": len(resolved),
        "pending_directional": mem["pending_directional"],
        "pending_hold": mem["pending_hold"],
        "resolved_hold": mem["resolved_hold"],
    }


def _format_seconds(sec: float) -> str:
    sec = int(sec)
    if sec >= 60:
        return f"{sec // 60}분 {sec % 60}초"
    return f"{sec}초"


def _krw(usd: float) -> str:
    # Space between ₩ and the digits: in large bold weights the Won
    # glyph's two horizontal strokes visually merge with the following
    # digit and look like a strikethrough. The space fixes that.
    return f"₩ {int(round(usd * _KRW_PER_USD)):,}"


def _stat_card(label: str, value: str, sub: str = "") -> str:
    if sub:
        # Newlines in `sub` become explicit <br> so multi-line subs
        # (e.g. accuracy card with a footnote) render correctly while
        # the rest of the content stays HTML-escaped.
        sub_escaped = _html.escape(sub).replace("\n", "<br>")
        sub_html = f'<div class="stat-sub">{sub_escaped}</div>'
    else:
        sub_html = ""
    return f"""
    <div class="stat-card">
      <div class="stat-label">{label}</div>
      <div class="stat-value">{value}</div>
      {sub_html}
    </div>
    """


def _render_stats_panel(stats: dict) -> str:
    if stats["total"] == 0:
        return ""

    # Card 1: 총 분석
    span_sub = ""
    if stats["first_date"] and stats["last_date"]:
        if stats["first_date"] == stats["last_date"]:
            span_sub = f"{stats['first_date']} ({stats['span_days']}일)"
        else:
            span_sub = (
                f"{stats['first_date']} ~ {stats['last_date']} "
                f"({stats['span_days']}일)"
            )
    card_total = _stat_card(
        "📊 총 분석", f"{stats['total']}건",
        span_sub + f" · {stats['ticker_distinct']}개 종목" if span_sub else "",
    )

    # Card 2: 비용 (오늘 / 이번 달)
    cost_label_parts = []
    for model in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        usd = stats["month_cost_by_model"].get(model, 0)
        if usd > 0:
            short = model.replace("gemini-2.5-", "")
            cost_label_parts.append(f"{short} {_krw(usd)}")
    cost_value = f"{_krw(stats['today_cost_usd'])} / {_krw(stats['month_cost_usd'])}"
    cost_sub_parts = [f"{stats['today_label']} / {stats['month_label']}"]
    if cost_label_parts:
        cost_sub_parts.append(" / ".join(cost_label_parts))
    # Per-subsystem breakdown (분석 / Screener / SV). Surface only buckets
    # with non-zero this-month cost — keeps the sub-label compact.
    sub_parts: list[str] = []
    for key, label in [("분석", "분석"), ("Screener", "screener"), ("Daily Byte", "Daily Byte"),
                       ("청약", "청약"), ("부동산", "부동산"), ("블로그", "블로그"), ("SV", "SV"),
                       ("수출입", "수출입")]:
        m_usd = stats["month_cost_by_sub_usd"].get(key, 0) or 0
        if m_usd > 0:
            sub_parts.append(f"{label} {_krw(m_usd)}")
    if sub_parts:
        cost_sub_parts.append("월 합산: " + " · ".join(sub_parts))
    # Surface upstream tool health on the same card. A persistent
    # yfinance / alpha vantage outage shows up here BEFORE it cascades
    # into analyst failures on the errors page, so the operator can
    # diagnose root cause without staring at journalctl.
    tool_fails = stats.get("tool_failures_24h") or {}
    if tool_fails:
        top = list(tool_fails.items())[:3]
        short = {
            "get_macro_context": "macro",
            "get_risk_metrics": "risk",
            "get_sector_relative_strength": "sector",
        }
        line = "⚠️ 24h 도구 실패 — " + ", ".join(
            f"{short.get(t, t)} ×{n}" for t, n in top
        )
        cost_sub_parts.append(line)
    card_cost = _stat_card(
        "💰 비용 (오늘 / 이번 달)", cost_value, "\n".join(cost_sub_parts),
    )

    # Card 3: 평균 시간
    top_part = (
        f"가장 많이 분석: {stats['top_ticker']} × {stats['top_count']}"
        if stats["top_count"] > 0 else ""
    )
    card_time = _stat_card(
        "⏱ 평균 분석 시간",
        _format_seconds(stats["avg_elapsed_sec"]),
        top_part,
    )

    # Card 4: 추천 정확도
    # Surface the denominator transparency so a 1/1 = 100% case doesn't
    # read as 'the bot has been right every time'. Show pending and
    # Hold-excluded counts alongside, plus a footnote explaining why
    # most analyses haven't matured into the score yet.
    pending_dir = stats.get("pending_directional", 0)
    pending_hold = stats.get("pending_hold", 0)
    resolved_hold = stats.get("resolved_hold", 0)
    hold_total = pending_hold + resolved_hold

    if stats["accuracy"] is not None:
        acc_pct = stats["accuracy"] * 100
        acc_value = f"{acc_pct:.0f}% ({stats['correct']}/{stats['evaluated']})"
    else:
        acc_value = "—"

    sub_parts = []
    sub_parts.append(f"평가 {stats['evaluated']}건")
    if pending_dir:
        sub_parts.append(f"미해소 {pending_dir}건")
    if hold_total:
        # Clarify that Hold's exclusion is *accuracy-denominator-only* — Hold
        # picks DO contribute to 평균 수익 / 알파. Previous label '(분모
        # 제외)' alone misled readers into 'Hold is excluded from every stat'.
        sub_parts.append(f"Hold {hold_total}건 (정확도 분모만 제외)")
    if stats["avg_return"] is not None:
        sub_parts.append(f"평균 수익 {stats['avg_return']:+.2f}%")
    if stats["avg_alpha"] is not None:
        # Rename '벤치 X%p' → '알파 X%p (vs 섹터 ETF)' — '벤치' alone reads
        # ambiguously as 'benchmark dropped X%p' instead of the intended
        # 'bot vs benchmark alpha = X%p' (negative = underperformance).
        sub_parts.append(f"알파 {stats['avg_alpha']:+.2f}%p (vs 섹터 ETF)")

    note = (
        "※ 정확도는 알파(raw 수익 − 섹터 ETF) 기준 · 분석 후 5거래일 경과 시"
        " 자동 평가 (백그라운드 12시간마다)"
    )
    acc_sub = " · ".join(sub_parts) + "\n" + note
    card_acc = _stat_card("🎯 추천 정확도", acc_value, acc_sub)

    return f"""
    <section class="stats-grid">
      {card_total}{card_cost}{card_time}{card_acc}
    </section>
    """


# ─── date / rating utilities ─────────────────────────────────────────
_DAY_OF_WEEK = ["월", "화", "수", "목", "금", "토", "일"]
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _ticker_display_name(ticker: str) -> str | None:
    """Return the human-readable company name for the ticker, or None.

    Resolves: KR → DART corp_name, JP → yfinance longName, TW →
    yfinance longName, US → None (US tickers are usually recognizable
    enough that the bare symbol works). Used by card / detail / search
    rendering so users see '삼성전자' / 'Toyota Motor Corporation' /
    'Taiwan Semiconductor Manufacturing Company Limited' instead of
    '005930.KS' / '7203.T' / '2330.TW' in a mixed-market card list.
    Falls back to None on any lookup miss (KR ETFs not in DART, JP/TW
    longName missing, etc.) and the caller renders the bare ticker."""
    try:
        from bot.market import detect_market
        m = detect_market(ticker)
        if m == "KR":
            from bot.dart_client import get_dart
            code = (ticker or "").upper().split(".")[0]
            return get_dart().stock_code_to_name(code)
        if m in ("JP", "TW"):
            # JP / TW both rely on yfinance longName — for JP it's
            # typically English ('Toyota Motor Corporation') or 漢字 +
            # English mix; for TW it's typically English corporate name
            # ('Taiwan Semiconductor Manufacturing Company Limited') with
            # some entries returning 繁體中文. Either is OK as a display
            # prefix — anything beats a bare '7203.T' / '2330.TW'.
            from tradingagents.agents.utils.agent_utils import _instrument_info
            info = _instrument_info(ticker) or {}
            name = info.get("longName") or info.get("shortName")
            if name and isinstance(name, str) and name.upper() != (ticker or "").upper():
                return name
            return None
    except Exception:
        pass
    return None


# Backwards-compat alias — the helper was KR-only originally; existing
# call sites still use _ticker_kr_name. Same callable, more general scope.
_ticker_kr_name = _ticker_display_name


def _format_date_kr(date_str: str) -> str:
    m = _DATE_RE.match(date_str)
    if not m:
        return date_str
    try:
        d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return f"{date_str} ({_DAY_OF_WEEK[d.weekday()]})"
    except Exception:
        return date_str


_RATING_BADGE = {
    "Buy":         ("매수",   "#16a34a"),
    "Overweight":  ("비중확대", "#22c55e"),
    "Hold":        ("보유",   "#737373"),
    "Underweight": ("비중축소", "#f97316"),
    "Sell":        ("매도",   "#dc2626"),
}


def _badge_html(rating: str) -> str:
    label, color = _RATING_BADGE.get(rating, (rating or "?", "#737373"))
    return f'<span class="badge" style="background:{color}">{_html.escape(label)}</span>'


# ─── markdown → HTML (minimal) ───────────────────────────────────────
# Telegram bot uses its own polish for Telegram HTML; the dashboard
# needs plain browser HTML. Keep it small: escape, then bold + headers.
_BOLD_RE = re.compile(r"\*\*([^\*\n]+?)\*\*")
_HEADER_RE = re.compile(r"(?m)^(#{1,4})\s+(.+)$")


def _md_to_html(text: str) -> str:
    if not text:
        return ""
    out = _html.escape(text)
    out = _BOLD_RE.sub(r"<strong>\1</strong>", out)

    def _h(m: re.Match) -> str:
        level = min(len(m.group(1)) + 2, 6)
        return f"<h{level}>{m.group(2)}</h{level}>"

    out = _HEADER_RE.sub(_h, out)
    # `<pre>` preserves newlines + indentation. Wrap with white-space:pre-wrap
    # via the .report class so long lines still break.
    return f'<pre class="report">{out}</pre>'


# ─── archive scan ────────────────────────────────────────────────────
def _load_all() -> list[dict]:
    out: list[dict] = []
    if not ARCHIVE_ROOT.exists():
        return out
    for day_dir in ARCHIVE_ROOT.iterdir():
        if not day_dir.is_dir() or not _DATE_RE.match(day_dir.name):
            continue
        for json_path in day_dir.glob("*.json"):
            try:
                rec = json.loads(json_path.read_text(encoding="utf-8"))
                out.append(rec)
            except Exception as exc:
                log.warning("dashboard: skip unreadable %s: %s", json_path, exc)
    return out


# ─── HTML rendering ──────────────────────────────────────────────────
# Light theme only. The earlier dark-mode media query rendered as a
# near-black background on phones whose OS was in dark mode, which the
# user found unreadable. Dropping the override means the page always
# uses the light palette regardless of system preference.
# Inline theme script — runs synchronously in <head> before <style>
# applies, so the dashboard never flashes the wrong theme on load.
# 19:00–07:00 KST → dark; rest → light. Re-checks every 60s so a
# tab left open across the boundary flips automatically.
_THEME_JS = """
(function() {
  function apply() {
    var h = parseInt(new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Seoul', hour: 'numeric', hour12: false
    }).format(new Date()), 10) % 24;
    var dark = (h >= 19 || h < 7);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }
  apply();
  setInterval(apply, 60000);
})();
"""


# Inline 태극기 SVG — 14×10 px. 🇰🇷 regional-indicator pair 는 일부 OS
# (특히 Linux Chromium 기본 폰트) 에서 'KR' 글자로 fallback 렌더되어
# 사용자가 "국기처럼" 인식 못 함. 인라인 SVG 로 환경 무관 보장. 4 괘
# 는 시각적 노이즈가 되어 생략 — 단색 흰 배경 + 청홍 태극 원으로 인식
# 충분 (14px 폭에서 4괘는 어차피 잡티). vertical-align:middle 로 텍스트
# baseline 정렬.
_KR_FLAG_SVG = (
    '<svg width="16" height="11" viewBox="0 0 60 40" '
    'style="vertical-align:-1px;display:inline-block" aria-label="한국 국기">'
    '<rect width="60" height="40" fill="#fff" stroke="#d0d0d0" stroke-width="0.5"/>'
    '<circle cx="30" cy="20" r="9" fill="#cd2e3a"/>'
    '<path d="M21 20 A9 4.5 0 0 1 39 20 A4.5 4.5 0 0 0 30 20 '
    'A4.5 4.5 0 0 1 21 20 Z" fill="#0047a0"/>'
    '</svg>'
)


_BASE_CSS = """
:root {
  --fg: #1f2937; --fg-soft: #6b7280; --bg: #f8fafc; --card: #ffffff;
  --border: #e5e7eb; --accent: #0ea5e9;
}
:root[data-theme="dark"] {
  --fg: #e5e7eb; --fg-soft: #9ca3af; --bg: #111827; --card: #1f2937;
  --border: #374151; --accent: #38bdf8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
    "Pretendard", "Helvetica Neue", "Segoe UI", "Apple Color Emoji",
    "Segoe UI Emoji", "Noto Color Emoji", "Twemoji Mozilla", sans-serif;
  color: var(--fg); background: var(--bg); line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--fg-soft); font-size: 13px; margin: 0 0 24px; }
.badge {
  font-size: 11px; padding: 2px 9px; border-radius: 999px;
  color: white; font-weight: 600; white-space: nowrap;
}
"""

_INDEX_CSS = _BASE_CSS + """
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; margin: 16px 0 8px;
}
.stat-card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px;
}
.stat-label {
  font-size: 12px; color: var(--fg-soft); margin-bottom: 6px;
  font-weight: 500;
}
.stat-value {
  font-size: 22px; font-weight: 700; color: var(--fg);
  line-height: 1.2;
}
.stat-sub {
  font-size: 11px; color: var(--fg-soft); margin-top: 6px;
  word-break: keep-all;
}
.search-bar {
  display: flex; align-items: center; gap: 8px; margin: 16px 0 24px;
  padding: 4px;
}
.search-bar input {
  flex: 1; padding: 10px 14px; font-size: 15px; color: var(--fg);
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  outline: none; transition: border-color 0.12s;
}
.search-bar input:focus { border-color: var(--accent); }
.search-bar button {
  padding: 10px 22px; font-size: 14px; font-weight: 500;
  cursor: pointer; white-space: nowrap;
  background: #22c55e; color: #ffffff;
  border: 1px solid transparent; border-radius: 9999px;
  transition: background 0.12s ease, transform 0.06s ease;
}
.search-bar button:hover { background: #16a34a; }
.search-bar button:active { transform: scale(0.97); }
:root[data-theme="dark"] .search-bar button { background: #16a34a; }
:root[data-theme="dark"] .search-bar button:hover { background: #15803d; }
.market-filter {
  display: flex; gap: 6px; flex-wrap: wrap; margin: -12px 4px 12px;
}
.mf-btn {
  background: var(--card); color: var(--fg-soft); border: 1px solid var(--border);
  border-radius: 16px; padding: 5px 14px; font-size: 13px; cursor: pointer;
  font-family: inherit; transition: border-color 0.12s, background 0.12s;
}
.mf-btn:hover { color: var(--fg); border-color: var(--accent); }
.mf-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
.mf-count { opacity: 0.7; font-size: 11px; margin-left: 2px; }
.status-line {
  color: var(--fg-soft); font-size: 13px; margin: 0 4px 16px;
}
.empty-search {
  color: var(--fg-soft); font-size: 14px; padding: 32px 0;
  text-align: center; display: none;
}
details.month { margin-bottom: 22px; }
summary.month-head {
  font-size: 17px; font-weight: 700; padding: 12px 4px; cursor: pointer;
  border-bottom: 2px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  list-style: none;
}
summary.month-head::-webkit-details-marker { display: none; }
summary.month-head::before {
  content: "▶"; display: inline-block; margin-right: 8px;
  transition: transform 0.15s; font-size: 11px; color: var(--fg-soft);
}
details.month[open] > summary.month-head::before { transform: rotate(90deg); }
summary.month-head .count {
  font-size: 12px; color: var(--fg-soft); font-weight: 400;
}
.month-body { padding-left: 6px; }
details.day { margin-bottom: 18px; }
summary.day-head {
  font-size: 16px; font-weight: 600; padding: 10px 4px; cursor: pointer;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  list-style: none;
}
summary.day-head::-webkit-details-marker { display: none; }
summary.day-head::before {
  content: "▶"; display: inline-block; margin-right: 8px;
  transition: transform 0.15s; font-size: 11px; color: var(--fg-soft);
}
details.day[open] > summary.day-head::before { transform: rotate(90deg); }
summary.day-head .count {
  font-size: 12px; color: var(--fg-soft); font-weight: 400;
}
.cards { display: grid; grid-template-columns: 1fr; gap: 8px; padding: 12px 0; }
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; transition: border-color 0.12s;
}
.card:hover { border-color: var(--accent); }
.card-row {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.ticker {
  font-weight: 700; font-size: 15px; color: var(--fg);
  text-decoration: none; min-width: 70px;
}
.ticker:hover { color: var(--accent); text-decoration: none; }
.stance {
  color: var(--fg-soft); font-size: 13px; flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.time { color: var(--fg-soft); font-size: 12px; min-width: 38px; text-align: right; }
.del-btn {
  background: none; border: none; cursor: pointer; padding: 4px 6px;
  color: #f87171; font-size: 14px; line-height: 1;
  transition: color 0.15s;
}
.del-btn:hover { color: #ef4444; }
:root[data-theme="dark"] .del-btn { color: #f87171; }
:root[data-theme="dark"] .del-btn:hover { color: #fca5a5; }
.past { color: var(--fg-soft); font-size: 12px; margin-top: 6px; }
.outcome { font-size: 12px; margin-top: 4px; color: var(--fg-soft); }
.outcome.hit { color: #10b981; }       /* directional call matched alpha */
.outcome.miss { color: #ef4444; }      /* directional call missed */
:root[data-theme="dark"] .outcome.hit { color: #34d399; }
:root[data-theme="dark"] .outcome.miss { color: #f87171; }
.orphan-day { opacity: 0.85; margin-top: 12px; }
.orphan-list { padding: 8px 12px; }
.orphan-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 6px 0; border-bottom: 1px solid var(--border);
}
.orphan-row:last-child { border-bottom: none; }
.orphan-meta { color: var(--fg-soft); font-size: 12px; }
.orphan-outcome { margin-top: 0; }
.empty {
  color: var(--fg-soft); font-size: 14px; padding: 32px 0; text-align: center;
}
.archive-footer {
  margin-top: 32px; padding-top: 16px;
  border-top: 1px solid var(--border);
  text-align: center; color: var(--fg-soft); font-size: 12px;
}
/* Snippet-highlight search panel (mirrors Bottleneck Screener pattern).
   Each snippet is an <a> wrapping section label + highlighted line; click
   navigates to the per-analysis detail page with #mark= so the detail
   page JS can scroll-to + highlight the source paragraph. */
.snippets {
  display: flex; flex-direction: column; gap: 8px;
  margin: 0 0 24px;
}
.snippet {
  background: var(--card); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 8px;
  padding: 10px 14px; text-decoration: none; color: inherit;
  transition: background 0.1s, border-color 0.1s;
  display: block;
}
.snippet:hover {
  background: rgba(14, 165, 233, 0.06);
  border-left-color: #38bdf8;
}
.snippet-meta {
  display: flex; gap: 10px; font-size: 11px;
  color: var(--fg-soft); margin-bottom: 4px; align-items: center;
  flex-wrap: wrap;
}
.snippet-sec {
  background: rgba(14, 165, 233, 0.12); color: var(--accent);
  padding: 2px 8px; border-radius: 4px; font-weight: 600;
  white-space: nowrap;
}
.snippet-card {
  font-family: 'IBM Plex Mono', 'JetBrains Mono', monospace;
  word-break: break-all;
}
.snippet-text {
  color: var(--fg); font-size: 13px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-word;
}
mark {
  background: rgba(245, 158, 11, 0.35); color: inherit;
  padding: 1px 3px; border-radius: 3px; font-weight: 600;
}
"""


_INDEX_JS = """
(function() {
  // Snippet-highlight search across BOTH card metadata (ticker / 한국명 /
  // stance / 평가 등) AND analyst body content (full_report indexed at
  // render time). Matches '변압기' / 'GLP-1' etc that live deep inside an
  // analysis surface as clickable snippets; clicking navigates to the
  // detail page with a #mark= hash so the detail-page JS scrolls to +
  // highlights the same line. Empty query restores the default card
  // list (full archive view); same UX as the Bottleneck Screener page.
  const searchEl = document.getElementById('search');
  const clearBtn = document.getElementById('clear-btn');
  const statusEl = document.getElementById('status');
  const emptyEl = document.getElementById('empty-search');
  const snp = document.getElementById('snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const days = Array.from(document.querySelectorAll('details.day'));
  const monthsG = Array.from(document.querySelectorAll('details.month'));
  const total = cards.length;
  const MAX_SNIPPETS = 80;
  var marketFilter = 'ALL';
  const mfBtns = Array.from(document.querySelectorAll('.mf-btn'));
  function matchesMarket(c) {
    return marketFilter === 'ALL' || c.dataset.market === marketFilter;
  }

  // Parse each card's body line index once. Synthetic 'metadata' line
  // adds the visible card-row text (ticker chip + stance + rating)
  // so a plain ticker query like 'NVDA' still surfaces the card as
  // a snippet rather than 0 matches when the body section labels
  // don't contain 'NVDA' literally.
  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const tk = (c.dataset.ticker || '').trim();
    const nm = (c.dataset.name || '').trim();
    const rowEl = c.querySelector('.card-row');
    const rowTxt = rowEl ? rowEl.textContent.replace(/\\s+/g, ' ').trim() : '';
    if (tk || nm || rowTxt) {
      lines.unshift({sec: '카드', txt: ((nm ? nm + ' · ' : '') + tk + ' · ' + rowTxt).trim()});
    }
    return {card: c, lines: lines};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function escapeReg(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function highlight(text, q) {
    if (!q) return escapeHtml(text);
    const re = new RegExp(escapeReg(q), 'gi');
    const safe = escapeHtml(text);
    let out = '', last = 0, m;
    while ((m = re.exec(safe)) !== null) {
      out += safe.slice(last, m.index);
      out += '<mark>' + safe.slice(m.index, m.index + m[0].length) + '</mark>';
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;
    }
    out += safe.slice(last);
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none';
    snp.innerHTML = '';
    emptyEl.style.display = 'none';
    var shown = 0;
    for (const c of cards) {
      var vis = matchesMarket(c);
      c.style.display = vis ? '' : 'none';
      if (vis) shown++;
    }
    for (const d of days) {
      var hasVis = !!d.querySelector('.card:not([style*="display: none"])');
      d.style.display = hasVis ? '' : 'none';
      if (hasVis && !d.classList.contains('orphan-day')) d.open = true;
    }
    for (const m of monthsG) {
      var hasDay = !!m.querySelector('details.day:not([style*="display: none"])');
      m.style.display = hasDay ? '' : 'none';
    }
    if (marketFilter === 'ALL') {
      statusEl.textContent = '총 ' + total + '건의 분석 기록';
    } else {
      statusEl.textContent = marketFilter + ' ' + shown + '건 / 총 ' + total + '건';
    }
  }

  function showSnippetsMode(q) {
    // Hide cards + day/month groups; snippet list becomes primary view.
    for (const c of cards) c.style.display = 'none';
    for (const d of days) d.style.display = 'none';
    for (const m of monthsG) m.style.display = 'none';

    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      if (!matchesMarket(cd.card)) continue;
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, sec: ln.sec, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }

    if (hits.length === 0) {
      snp.style.display = 'none';
      emptyEl.style.display = 'block';
      statusEl.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emptyEl.style.display = 'none';

    const cardHitCounts = new Map();
    const parts = [];
    for (const h of hits) {
      const cid = h.card.id;
      cardHitCounts.set(cid, (cardHitCounts.get(cid) || 0) + 1);
      const ticker = h.card.dataset.ticker || '';
      const name = h.card.dataset.name || '';
      const date = h.card.dataset.date || '';
      const href = h.card.dataset.href || '#';
      const cardLabel = (name ? name + ' / ' : '') + ticker;
      const sec = h.sec || '본문';
      parts.push(
        '<a class="snippet" href="' + href +
          '#mark=' + encodeURIComponent(q) +
          '" data-target="' + cid + '">' +
          '<div class="snippet-meta">' +
            '<span class="snippet-sec">' + escapeHtml(sec) + '</span>' +
            '<span class="snippet-card">' + escapeHtml(cardLabel) +
            ' · ' + escapeHtml(date) + '</span>' +
          '</div>' +
          '<div class="snippet-text">' + highlight(h.txt, q) + '</div>' +
        '</a>'
      );
    }
    snp.innerHTML = parts.join('');
    snp.style.display = 'flex';
    const uniq = cardHitCounts.size;
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건 표시)' : '';
    statusEl.textContent = hits.length + '개 라인 · ' + uniq + '개 분석 매칭' + cap +
                            ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const raw = (searchEl.value || '').trim();
    if (!raw) { showCardsMode(); return; }
    showSnippetsMode(raw);
  }

  function syncFromHash() {
    const m = (location.hash || '').match(/^#ticker=([A-Za-z0-9.]+)/);
    if (m) searchEl.value = m[1].toUpperCase();
    applyFilter();
  }

  searchEl.addEventListener('input', applyFilter);
  clearBtn.addEventListener('click', function() {
    searchEl.value = '';
    if (location.hash) history.replaceState(null, '', location.pathname);
    applyFilter();
    searchEl.focus();
  });
  window.addEventListener('hashchange', syncFromHash);
  syncFromHash();

  mfBtns.forEach(function(btn) {
    btn.addEventListener('click', function() {
      marketFilter = btn.dataset.mf;
      mfBtns.forEach(function(b) { b.classList.toggle('active', b === btn); });
      applyFilter();
    });
  });

  // Card deletion: POST /api/delete with {date, ticker}, then remove the
  // card from the DOM on success. We don't location.reload() because
  // that would kick the user back to the top of the page; the
  // regenerate_index() call on the server side has already rewritten
  // index.html so a manual refresh later picks up everything.
  document.querySelectorAll('.del-btn').forEach(function(btn) {
    btn.addEventListener('click', function(ev) {
      ev.stopPropagation();
      ev.preventDefault();
      const card = btn.closest('.card');
      if (!card) return;
      const date = card.dataset.date;
      const ticker = card.dataset.ticker;
      if (!date || !ticker) return;
      if (!confirm('📊 ' + ticker + ' (' + date + ') 분석 기록을 삭제할까요?')) return;
      btn.disabled = true;
      btn.textContent = '⏳';
      fetch('api/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({date: date, ticker: ticker})
      }).then(function(r) {
        return r.json().then(function(d) { return {status: r.status, body: d}; });
      }).then(function(res) {
        if (res.status === 200 && res.body && res.body.ok) {
          card.style.transition = 'opacity 0.2s';
          card.style.opacity = '0';
          setTimeout(function() { card.remove(); }, 200);
        } else {
          alert('삭제 실패: ' + (res.body && res.body.error || res.status));
          btn.disabled = false;
          btn.textContent = '🗑️';
        }
      }).catch(function(err) {
        alert('삭제 실패: ' + err);
        btn.disabled = false;
        btn.textContent = '🗑️';
      });
    });
  });

  // ── Scroll position persistence ──────────────────────────────
  // Save clicked card ID before navigating to detail page.
  // On return (page reload), find the card, open collapsed parents,
  // and scroll it into view.
  document.addEventListener('click', function(ev) {
    var link = ev.target.closest('a.ticker');
    if (!link) return;
    var card = link.closest('.card');
    if (card && card.id) sessionStorage.setItem('noah_return', card.id);
  });
  (function restoreScroll() {
    var cid = sessionStorage.getItem('noah_return');
    if (!cid) return;
    sessionStorage.removeItem('noah_return');
    var el = document.getElementById(cid);
    if (!el) return;
    var p = el.parentElement;
    while (p) {
      if (p.tagName === 'DETAILS' && !p.open) p.open = true;
      p = p.parentElement;
    }
    requestAnimationFrame(function() { el.scrollIntoView({block:'center'}); });
  })();
})();
"""


_ERRORS_CSS = _BASE_CSS + """
.section-head {
  font-size: 16px; font-weight: 600; padding: 10px 4px;
  border-bottom: 1px solid var(--border);
  margin: 24px 0 12px;
}
.issue-card {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 14px; margin-bottom: 8px;
}
.issue-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin-bottom: 6px;
}
.issue-head .ticker {
  font-weight: 700; font-size: 15px; color: var(--fg); text-decoration: none;
}
.issue-head .ticker:hover { color: var(--accent); }
.issue-head .when { color: var(--fg-soft); font-size: 12px; margin-left: auto; }
.issue-list { color: var(--fg); font-size: 13px; line-height: 1.6; }
.issue-list .item { color: #f97316; }
.fail-row {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 14px; margin-bottom: 6px;
}
.fail-row .ticker { font-weight: 700; }
.fail-row .reason { color: #dc2626; flex: 1; min-width: 0; }
.fail-row .when { color: var(--fg-soft); font-size: 12px; }
.empty {
  color: var(--fg-soft); font-size: 14px; padding: 24px 0; text-align: center;
}
.back { color: var(--fg-soft); font-size: 13px; }
.back:hover { color: var(--accent); }
"""


def _render_errors_page(
    records: list[dict],
    hard_failures: list[dict],
) -> str:
    """Build errors.html — a chronological catalog of analyses that
    completed with placeholder/error sections (soft issues only).

    Hard failures (timeout / exception) are deliberately NOT shown here:
    the user gets an immediate Telegram error message at the time of the
    failure, so re-listing them on the dashboard is redundant noise.
    The records still live in usage.jsonl for /usage and tool-failure
    aggregation; we just don't surface them on this catalog page.
    """
    _ = hard_failures  # accepted for API compatibility; intentionally unused
    soft: list[tuple[dict, list[str]]] = []
    for r in records:
        issues = _detect_issues(r)
        if issues:
            soft.append((r, issues))
    # Newest first
    soft.sort(
        key=lambda pair: (
            pair[0].get("trade_date", ""), pair[0].get("analyzed_at", "")
        ),
        reverse=True,
    )

    hard_section = ""

    # ── soft issues, grouped by date ──
    if soft:
        by_date: dict[str, list[tuple[dict, list[str]]]] = {}
        for r, issues in soft:
            by_date.setdefault(r["trade_date"], []).append((r, issues))

        soft_groups = []
        for date in sorted(by_date.keys(), reverse=True):
            day_items = by_date[date]
            cards_html = []
            for r, issues in day_items:
                ticker = r.get("ticker", "?")
                analyzed_at = r.get("analyzed_at") or ""
                time_str = analyzed_at[11:16] if len(analyzed_at) >= 16 else ""
                href = f"./{date}/{_html.escape(ticker)}.html"
                rating = _extract(_RATING_RE, r.get("summary", "")) or "?"
                # KR tickers show their Korean corp name on this catalog
                # page too — the user reported the errors panel was the
                # last place still rendering bare numeric tickers
                # (019680/014680/039030 in their 2026-05-17 view).
                kr_name = _ticker_kr_name(ticker)
                label = kr_name or ticker
                items_html = "".join(
                    f'<div class="item">⚠️ {_html.escape(i)}</div>'
                    for i in issues
                )
                cards_html.append(f"""
                <div class="issue-card">
                  <div class="issue-head">
                    <a class="ticker" href="{href}">📊 {_html.escape(label)}</a>
                    {_badge_html(rating)}
                    <span class="when">{_html.escape(time_str)}</span>
                  </div>
                  <div class="issue-list">{items_html}</div>
                </div>
                """)
            soft_groups.append(f"""
            <div class="section-head">
              📅 {_format_date_kr(date)} — {len(day_items)}건 부분 미완성
            </div>
            {"".join(cards_html)}
            """)
        soft_section = "".join(soft_groups)
    else:
        soft_section = '<div class="empty">부분 미완성 케이스가 없습니다.</div>'

    body_empty = not soft
    if body_empty:
        body = '<div class="empty">부분 미완성 케이스가 없습니다. 🎉</div>'
    else:
        body = soft_section

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🚨 NOAH 오류 / 미완성 기록</title>
<script>{_THEME_JS}</script>
<style>{_ERRORS_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="./index.html">← 아카이브로 돌아가기</a>
  <h1 style="margin-top:8px">🚨 오류 / 미완성 분석 기록</h1>
  <p class="sub">분석가 부분 미완성 (도구 실패·섹션 placeholder·사과형 응답)만 추적합니다. 하드 실패는 텔레그램에서 즉시 알림됨.</p>
  {body}
</div>
</body>
</html>
"""


def _count_total_issues(records: list[dict], hard_failures: list[dict]) -> int:
    """Count only soft (partial-completion) issues for the index nav badge.
    Hard failures are excluded — they're shown to the user via Telegram in
    real time and don't belong on this catalog page anymore."""
    _ = hard_failures  # accepted for compatibility; no longer counted
    n = 0
    for r in records:
        if _detect_issues(r):
            n += 1
    return n


def _build_resolved_lookup() -> dict[tuple[str, str], dict]:
    """Map (trade_date, ticker) → resolved memory-log entry. Used by the
    index card renderer to surface 5-trading-day realized return + alpha
    next to each historical analysis once auto_resolve has caught up."""
    mem = _read_memory_full()
    return {(e["date"], e["ticker"]): e for e in mem.get("resolved", [])}


def _rating_direction(rating: str) -> str:
    """Classify a verdict string into up / down / hold for chart markers."""
    r = (rating or "").lower()
    if any(k in r for k in ("buy", "overweight", "매수")):
        return "up"
    if any(k in r for k in ("sell", "underweight", "매도")):
        return "down"
    return "hold"


def _ticker_analysis_markers(
    records_for_ticker: list[dict], resolved_lookup: dict
) -> list[dict]:
    """Build chart markers for every past analysis of one ticker — the
    analysis date, its verdict (Buy/Hold/Sell…), and the resolved 5-day
    return when available. Lets the chart visualize our own track record
    on the stock (▲매수 +8% etc.). Sorted by date ascending (lightweight-
    charts requires ascending marker times)."""
    out: list[dict] = []
    for r in records_for_ticker:
        date = r.get("trade_date")
        ticker = r.get("ticker", "")
        if not date:
            continue
        rating = _extract(_RATING_RE, r.get("summary", "") or "") or ""
        if not rating:
            continue
        res = resolved_lookup.get((date, ticker))
        ret = None
        if res:
            ret = _parse_pct((res.get("raw") or "").strip())
        out.append({
            "time": date,
            "dir": _rating_direction(rating),
            "rating": rating.strip(),
            "ret": ret,
        })
    out.sort(key=lambda m: m["time"])
    return out


_BENCHMARK_LABEL_CACHE: dict[str, str] = {}


def _benchmark_label_for(ticker: str) -> str:
    """Return the sector ETF human-readable label for this ticker's alpha
    benchmark — e.g. '반도체 (SOXX)' for AMAT, '금융 (XLF)' for JPM,
    'KOSPI 200 (KODEX 200)' for KR blue chips, 'TOPIX (1306)' for JP.

    Uses `_resolve_benchmark`'s tuple[1] (the Korean label that already
    has the ETF ticker in parens) rather than tuple[0] (raw ticker
    only). The richer label is much clearer to Korean readers than
    bare codes like '069500.KS' — surfaced by the cross-market audit
    2026-05-18 where KR/JP outcome lines were less informative than
    US ones due to numeric ticker codes.

    Falls back to 'SPY' when nothing resolves. Process-level cache
    keeps yfinance .info round-trips bounded across dashboard regens."""
    if ticker in _BENCHMARK_LABEL_CACHE:
        return _BENCHMARK_LABEL_CACHE[ticker]
    label = "SPY"
    try:
        from tradingagents.agents.utils.sector_strength_tools import _resolve_benchmark
        bm = _resolve_benchmark(ticker)
        if bm:
            # Prefer the Korean label (bm[1]) — already contains the
            # ETF ticker in parens. Fall back to bare ticker (bm[0])
            # only if the label slot is empty for some reason.
            label = bm[1] or bm[0] or "SPY"
    except Exception:
        pass
    _BENCHMARK_LABEL_CACHE[ticker] = label
    return label


def _render_outcome_html(resolved: dict | None) -> str:
    """Render outcome lines beneath an analysis card. Stacks 5d / 15d /
    30d when each is available — the 5d outcome lives in the entry tag
    (resolved['raw'] / resolved['alpha']) while longer windows live in
    resolved['outcomes_extra'] (list of {days, raw, alpha}). Empty when
    the entry is still pending or no realized return is parsed.

    Each window renders as its own line with independent hit/miss color
    so a Sell call that scores ✓ at 5d but reverses to ✗ at 30d is
    visible at a glance.

    Format (per line):
        📒 Nd 후 -1.6% (알파 +3.0%p vs 반도체 (SOXX)) — 매도 추천 맞음 · 섹터대비 high

    User-controllable knobs that influence this:
    - Direction verdict ('매수/매도 추천 맞음/틀림') from rating + alpha sign.
    - Magnitude ('섹터대비 high/low/동등') from alpha sign alone.
    - Benchmark label from _benchmark_label_for (sector ETF or broad
      market fallback per the ticker's industry / market).
    - CSS hit/miss class for color cue."""
    if not resolved:
        return ""
    rating = (resolved.get("rating") or "").lower()
    ticker = (resolved.get("ticker") or "").strip()

    # Collect windows in display order: 5d (from tag) first, then 15d
    # and 30d (from outcomes_extra). Filter unparseable rows so a
    # half-written OUTCOMES block doesn't crash the renderer.
    windows: list[dict] = []
    raw_5d = (resolved.get("raw") or "").strip()
    alpha_5d = (resolved.get("alpha") or "").strip()
    # Use _parse_pct as the validity gate — rejects "n/a", "+nan%", "+inf%"
    if _parse_pct(raw_5d) is not None:
        windows.append({"days": 5, "raw": raw_5d, "alpha": alpha_5d})
    for entry in (resolved.get("outcomes_extra") or []):
        days = entry.get("days")
        raw = (entry.get("raw") or "").strip()
        alpha = (entry.get("alpha") or "").strip()
        if not isinstance(days, int) or _parse_pct(raw) is None:
            continue
        windows.append({"days": days, "raw": raw, "alpha": alpha})
    if not windows:
        return ""
    windows.sort(key=lambda w: w["days"])  # 5 → 15 → 30

    bench_label = _benchmark_label_for(ticker) if ticker else "섹터"
    bench_safe = _html.escape(bench_label)

    lines: list[str] = []
    for w in windows:
        days = w["days"]
        raw = w["raw"]
        alpha = w["alpha"]
        cls = "outcome"
        verdict_text = ""
        alpha_num = _parse_pct(alpha)
        if alpha_num is not None and ticker:
            magnitude = "섹터대비 high" if alpha_num > 0 else (
                "섹터대비 low" if alpha_num < 0 else "섹터대비 동등"
            )
            if rating in ("buy", "overweight"):
                hit = alpha_num > 0
                cls += " hit" if hit else " miss"
                direction = "매수 추천 맞음" if hit else "매수 추천 틀림"
                verdict_text = f"{direction} · {magnitude}"
            elif rating in ("sell", "underweight"):
                hit = alpha_num < 0
                cls += " hit" if hit else " miss"
                direction = "매도 추천 맞음" if hit else "매도 추천 틀림"
                verdict_text = f"{direction} · {magnitude}"
            elif rating == "hold":
                verdict_text = f"보유 추천 · {magnitude}"
        alpha_part = (
            f" (알파 {_html.escape(alpha)}p vs {bench_safe})"
            if alpha and alpha != "n/a"
            else ""
        )
        verdict_part = f" — {_html.escape(verdict_text)}" if verdict_text else ""
        lines.append(
            f'<div class="{cls}">📒 {days}거래일 후 '
            f'{_html.escape(raw)}{alpha_part}{verdict_part}</div>'
        )
    return "".join(lines)


def _ticker_market(ticker: str) -> str:
    t = (ticker or "").upper()
    if t.endswith((".KS", ".KQ")): return "KR"
    if t.endswith(".T"): return "JP"
    if t.endswith((".TW", ".TWO")): return "TW"
    if t.endswith((".SS", ".SZ", ".BJ")): return "CN"
    if t.endswith(".HK"): return "HK"
    # Bare 6-digit numeric (no suffix) = Korean KOSPI/KOSDAQ code
    # (e.g. '039030' 이오테크닉스, '014680' 한솔케미칼). JP/TW codes are
    # 4-digit + suffix, CN 6-digit carry .SS/.SZ — so an UNsuffixed 6-digit
    # is unambiguously KR. Portfolio holdings resolved via DART store the
    # bare code; without this they fell through to US (이오테크닉스 → US bug).
    if len(t) == 6 and t.isdigit(): return "KR"
    return "US"


def _holding_market(h: dict) -> str:
    """Portfolio holding → market code. Uses resolve_ticker's market field
    first (covers DART-matched KR items). Falls back to ticker suffix.
    Unmatched holdings (ticker=None, market=None) default to KR — all data
    comes from 뱅크샐러드 Korean brokerage exports (ETFs like TIGER/KODEX,
    unlisted names like 그린광학/SK에코플랜트)."""
    mkt = h.get("market")
    if mkt:
        return mkt
    tkr = h.get("ticker")
    if tkr:
        return _ticker_market(str(tkr))
    return "KR"


def _render_index(records: list[dict]) -> str:
    by_date: dict[str, list[dict]] = {}
    for r in records:
        by_date.setdefault(r["trade_date"], []).append(r)

    # Market counts for filter buttons
    _market_counts: dict[str, int] = {}
    for _r in records:
        _mk = _ticker_market(_r.get("ticker", ""))
        _market_counts[_mk] = _market_counts.get(_mk, 0) + 1

    if not records:
        body = '<div class="empty">아직 분석 기록이 없습니다.</div>'
    else:
        # One memory-log read per index render; reused across every card.
        resolved_lookup = _build_resolved_lookup()
        sections = []
        # 월(YYYY-MM) 그룹 collapse (사용자 2026-06-01 — 모든 date-group
        # 대시보드 통일). 이번 달 펼침, 과거 달 접힘. day 그룹을 month
        # <details> 안에 nest. orphan 그룹은 월 밖(아래)에 유지.
        _kst_idx = datetime.timezone(datetime.timedelta(hours=9))
        _this_month_idx = datetime.datetime.now(_kst_idx).date().isoformat()[:7]
        _month_counts_idx: dict[str, int] = {}
        for _d in by_date:
            _ym = (_d or "")[:7]
            _month_counts_idx[_ym] = _month_counts_idx.get(_ym, 0) + len(by_date[_d])
        _prev_month_idx = None
        for date in sorted(by_date.keys(), reverse=True):
            _cur_month_idx = (date or "")[:7]
            if _cur_month_idx != _prev_month_idx:
                if _prev_month_idx is not None:
                    sections.append("</div></details>")  # close prev month
                _m_open = " open" if _cur_month_idx == _this_month_idx else ""
                sections.append(f"""
            <details class="month"{_m_open}>
              <summary class="month-head">
                <span>📆 {_month_kr(_cur_month_idx)}</span>
                <span class="count">{_month_counts_idx.get(_cur_month_idx, 0)}건</span>
              </summary>
              <div class="month-body">
            """)
                _prev_month_idx = _cur_month_idx
            day_records = sorted(
                by_date[date],
                key=lambda r: r.get("analyzed_at", ""),
                reverse=True,
            )
            cards = []
            for rec in day_records:
                summary_text = rec.get("summary", "") or ""
                rating = _extract(_RATING_RE, summary_text) or "?"
                stance = _extract(_STANCE_LINE_RE, summary_text)
                past = _extract(_PAST_OUTCOMES_RE, summary_text)
                analyzed_at = rec.get("analyzed_at") or ""
                time_str = analyzed_at[11:16] if len(analyzed_at) >= 16 else ""
                ticker = rec.get("ticker", "?")
                href = f"./{date}/{_html.escape(ticker)}.html"
                past_html = (
                    f'<div class="past">{_html.escape(past)}</div>' if past else ""
                )
                # Show realized 5-day outcome if auto_resolve has caught up.
                # Empty string before the window elapses or when the entry
                # never made it into the memory log (older runs, Hold-only).
                outcome_html = _render_outcome_html(
                    resolved_lookup.get((date, ticker))
                )
                # For KR tickers with a DART match, display the Korean
                # corp name instead of the raw '005930.KS' / '014680.KS'
                # numeric ticker — pure digits are hard to scan in a
                # mixed-market list. data-name is added so JS search
                # matches the Korean name too (in addition to the
                # ticker via data-ticker).
                kr_name = _ticker_kr_name(ticker)
                label = kr_name or ticker
                data_name_attr = (
                    f' data-name="{_html.escape(kr_name)}"' if kr_name else ""
                )
                # Snippet-search line index — built from full_report at
                # render time so '변압기' / 'GLP-1' / 'CHIPS Act' etc.
                # match anywhere in the analyst body, not just card
                # metadata. JSON-encoded then HTML-escaped so quotes /
                # angle brackets inside analyst text can't break the
                # data attribute. Lines list capped at 200 (front 100 +
                # back 100 when overflow) per card; ~50 KB / card max.
                _idx_lines = _index_lines_from_full_report(
                    rec.get("full_report") or ""
                )
                _idx_lines.append(
                    {"sec": "요약", "txt": (rec.get("summary") or "").strip()[:600]}
                )
                _lines_attr = _html.escape(
                    json.dumps(_idx_lines, ensure_ascii=False)
                )
                _card_mkt = _ticker_market(ticker)
                cards.append(f"""
                <div class="card" id="card-{_html.escape(date)}-{_html.escape(ticker).replace('.','_')}" data-ticker="{_html.escape(ticker)}"{data_name_attr} data-market="{_card_mkt}" data-date="{_html.escape(date)}" data-href="{href}" data-lines="{_lines_attr}">
                  <div class="card-row">
                    <a class="ticker" href="{href}">📊 {_html.escape(label)}</a>
                    {_badge_html(rating)}
                    <div class="stance">{_html.escape(stance)}</div>
                    <div class="time">{_html.escape(time_str)}</div>
                    <button class="del-btn" type="button" title="이 분석 기록 삭제">🗑️</button>
                  </div>
                  {past_html}
                  {outcome_html}
                </div>
                """)
            sections.append(f"""
            <details class="day" open>
              <summary class="day-head">
                <span>📅 {_format_date_kr(date)}</span>
                <span class="count">{len(day_records)}건</span>
              </summary>
              <div class="cards">{"".join(cards)}</div>
            </details>
            """)
        if _prev_month_idx is not None:
            sections.append("</div></details>")  # close final month
        # Orphan resolved entries: in the memory log but with no matching
        # archive record (typically analyses that predate the archive
        # system rollout). Surface them in a small footer so the
        # accuracy card's "평가 N건" denominator stays auditable — the
        # user can see exactly which past calls feed the percentage even
        # when there's no card to attach the outcome to.
        archive_keys = {(r.get("trade_date", ""), r.get("ticker", "")) for r in records}
        orphans = [
            e for (k, e) in resolved_lookup.items() if k not in archive_keys
        ]
        if orphans:
            orphans.sort(key=lambda e: (e.get("date", ""), e.get("ticker", "")), reverse=True)
            orphan_rows = []
            for e in orphans:
                row_outcome = _render_outcome_html(e).replace(
                    'class="outcome', 'class="outcome orphan-outcome'
                )
                orphan_rows.append(f"""
                <div class="orphan-row">
                  <span class="orphan-meta">📊 {_html.escape(e.get('ticker', '?'))}
                    · {_html.escape(e.get('date', ''))}
                    · {_html.escape(e.get('rating', ''))}</span>
                  {row_outcome}
                </div>
                """)
            sections.append(f"""
            <details class="day orphan-day">
              <summary class="day-head">
                <span>📒 archive 이전 평가 결과</span>
                <span class="count">{len(orphans)}건</span>
              </summary>
              <div class="orphan-list">{"".join(orphan_rows)}</div>
            </details>
            """)
        body = "".join(sections)

    stats_panel = _render_stats_panel(_compute_stats(records))
    # Footer line shown at the bottom of the page so the user can see at
    # a glance when the dashboard was regenerated and that the archive
    # has no rotation policy. Matches the convention used by the
    # search_my_brain dashboard the user asked us to mirror.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    generated_at = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M")
    footer_html = (
        f'<div class="archive-footer">'
        f'생성: {generated_at} · {len(records)}건 누적 · 무제한 보관'
        f'</div>'
    )
    # Headline link to the errors page; count includes hard failures
    # (usage.jsonl) plus archive entries with placeholder/tool issues.
    issue_count = _count_total_issues(records, _read_hard_failures())
    # External dashboards live at known LAN addresses; rel=noopener on the
    # external links prevents window.opener leakage to the third-party tab.
    # 🇰🇷 regional-indicator pair은 일부 OS (특히 Linux Chromium) 에서 'KR'
    # 글자로 fallback 렌더되므로 inline SVG 태극기로 대체 — 어디서나 보장.
    _external_links = (
        ' · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>'
        f' · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>'
    )
    # 부동산은 주간(느린) surface 라 nav 제일 뒤 (사용자 정책 2026-05-31)
    if issue_count > 0:
        errors_link = (
            ' · <a href="portfolio.html">💼 자산</a>'
            ' · <a href="budget.html">📒 가계부</a>'
            f' · <a href="errors.html">🚨 오류 / 미완성 {issue_count}건</a>'
            f' · <a href="screener.html">📊 Bottleneck Screener</a>'
            f' · <a href="screener_domains.html">🗂️ 도메인 목록</a>'
            f' · <a href="reddit_insider.html">📨 미국 레딧</a>'
            f' · <a href="daily_byte.html">📊 Daily Byte</a>'
            + _external_links
            + ' · <a href="realestate.html">🏠 부동산</a>'
            + ' · <a href="cheongyak.html">🎟️ 청약</a>'
            + ' · <a href="watchlist.html">🔔 워치리스트</a>'
            + ' · <a href="paper.html">🧪 페이퍼</a>'
            + ' · <a href="screen.html">📊 조건부 스크리너</a>'
        )
    else:
        errors_link = (
            ' · <a href="portfolio.html">💼 자산</a>'
            ' · <a href="budget.html">📒 가계부</a>'
            ' · <a href="errors.html">🚨 오류 기록 (없음)</a>'
            ' · <a href="screener.html">📊 Bottleneck Screener</a>'
            ' · <a href="screener_domains.html">🗂️ 도메인 목록</a>'
            ' · <a href="reddit_insider.html">📨 미국 레딧</a>'
            ' · <a href="daily_byte.html">📊 Daily Byte</a>'
            + _external_links
            + ' · <a href="realestate.html">🏠 부동산</a>'
            + ' · <a href="cheongyak.html">🎟️ 청약</a>'
            + ' · <a href="watchlist.html">🔔 워치리스트</a>'
            + ' · <a href="paper.html">🧪 페이퍼</a>'
            + ' · <a href="screen.html">📊 조건부 스크리너</a>'
        )

    # Market filter buttons (show only if >1 market present)
    _MKT_ORDER = ["US", "KR", "JP", "TW", "CN", "HK"]
    _mf_btns = [
        f'<button class="mf-btn active" data-mf="ALL">전체'
        f' <span class="mf-count">{len(records)}</span></button>'
    ]
    for _mk in _MKT_ORDER:
        if _mk in _market_counts:
            _mf_btns.append(
                f'<button class="mf-btn" data-mf="{_mk}">{_mk}'
                f' <span class="mf-count">{_market_counts[_mk]}</span></button>'
            )
    for _mk in sorted(_market_counts):
        if _mk not in _MKT_ORDER:
            _mf_btns.append(
                f'<button class="mf-btn" data-mf="{_mk}">{_mk}'
                f' <span class="mf-count">{_market_counts[_mk]}</span></button>'
            )
    market_filter_html = (
        f'<div class="market-filter" id="market-filter">{"".join(_mf_btns)}</div>'
        if len(_market_counts) > 1 else ""
    )

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🦉 NOAH 주식분석 아카이브</title>
<script>{_THEME_JS}</script>
<style>{_INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>🦉 NOAH 주식분석 아카이브</h1>
  <p class="sub">카드 클릭 시 전체 리포트{errors_link}</p>
  {stats_panel}
  <div class="search-bar">
    <input id="search" type="text" placeholder="종목 / 본문 검색 (예: NVDA, 삼성전자, 변압기, GLP-1, CHIPS Act)" autocomplete="off" spellcheck="false">
    <button id="clear-btn" type="button" title="검색 초기화">초기화</button>
  </div>
  {market_filter_html}
  <p id="status" class="status-line">총 {len(records)}건의 분석 기록</p>
  <div id="snippets" class="snippets" style="display:none"></div>
  <div id="empty-search" class="empty-search">검색 결과가 없습니다.</div>
  {body}
  {footer_html}
</div>
<script>{_INDEX_JS}</script>
</body>
</html>
"""


_DETAIL_CSS = _BASE_CSS + """
.back { color: var(--fg-soft); font-size: 13px; }
.back:hover { color: var(--accent); }
.title-row {
  display: flex; align-items: center; gap: 12px; margin: 14px 0 6px;
  flex-wrap: wrap;
}
.title-row h1 { margin: 0; font-size: 24px; }
.meta { color: var(--fg-soft); font-size: 13px; margin-bottom: 24px; }
.chart-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
.chart-tf-sep { color: var(--fg-soft); margin: 0 2px; }
.chart-tf-group { display: inline-flex; gap: 4px; }
.chart-tf-btn {
  background: var(--card); color: var(--fg-soft); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer;
  font-family: inherit; line-height: 1.6;
}
.chart-tf-btn:hover { color: var(--fg); border-color: var(--accent); }
.chart-tf-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
.chart-tf-status { color: var(--fg-soft); font-size: 12px; margin-left: 6px; }
.chart-ind-toolbar { margin-top: 4px; }
.chart-ind-label { color: var(--fg-soft); font-size: 12px; margin-right: 2px; }
.chart-ind-btn {
  background: var(--card); color: var(--fg-soft); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer;
  font-family: inherit; line-height: 1.6;
}
.chart-ind-btn:hover { color: var(--fg); border-color: var(--accent); }
.chart-ind-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: 600; }
.chart-row { display: flex; gap: 10px; align-items: stretch; }
.chart-main { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
/* 상단 헤드라인(현재가 large + 기간 수익률 + 거래량) — 레퍼런스 터미널 패턴. */
.chart-headline { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  margin: 2px 0; font-variant-numeric: tabular-nums; min-height: 24px; }
.chart-headline .ch-cur { font-size: 22px; font-weight: 700; color: var(--fg); }
.chart-headline .ch-chg { font-size: 13px; font-weight: 600; }
.chart-headline .ch-vol { font-size: 12px; color: var(--fg-soft); }
/* OHLC 상단바 — 마지막 봉(기본)·hover 봉의 날짜·시고저종·등락·이평선. */
.chart-ohlc { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  min-height: 16px; font-size: 11.5px; color: var(--fg-soft);
  font-variant-numeric: tabular-nums; margin-bottom: 2px; }
.chart-ohlc .co-date { color: var(--fg); font-weight: 600; }
.chart-ohlc .co-seg { white-space: nowrap; }
.price-chart { width: 100%; height: 480px; position: relative; }
.chart-tooltip {
  position: absolute; z-index: 5; pointer-events: none;
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  padding: 6px 9px; font-size: 11px; line-height: 1.55; white-space: nowrap;
  box-shadow: 0 2px 10px rgba(0,0,0,0.25); font-variant-numeric: tabular-nums;
}
.chart-tooltip .tt-date { color: var(--fg-soft); margin-bottom: 2px; font-size: 10px; }
.chart-disc {
  margin: 6px 0 2px; padding: 7px 11px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--card); font-size: 12.5px; line-height: 1.55;
  color: var(--fg-soft); min-height: 20px;
}
.chart-disc b { color: var(--fg); }
.chart-disc a { color: var(--accent); text-decoration: none; }
.chart-disc a:hover { text-decoration: underline; }
.chart-disc .cd-empty { opacity: .7; }
.chart-disc .cd-desc { opacity: .75; }
.sub-chart { width: 100%; height: 120px; }
.sub-chart.hidden { display: none; }
.chart-values {
  flex: 0 0 138px; display: flex; flex-direction: column; gap: 5px;
  padding: 4px 0 0 14px; margin-left: 6px; font-size: 12px;
  border-left: 1px solid var(--border);
}
.cv-sep { border-top: 1px solid var(--border); margin: 2px 0; }
.cv-item { display: flex; align-items: baseline; gap: 5px; }
.cv-dot { flex: 0 0 8px; width: 8px; height: 8px; border-radius: 2px; align-self: center; }
.cv-name { color: var(--fg-soft); flex: 1 1 auto; word-break: keep-all; }
.cv-val { font-weight: 600; font-variant-numeric: tabular-nums; }
@media (max-width: 560px) {
  .chart-row { flex-direction: column; }
  .chart-values { flex: 0 0 auto; flex-direction: row; flex-wrap: wrap; gap: 4px 14px;
    border-left: none; margin-left: 0; padding-left: 0; }
  .cv-sep { display: none; }
}
.chart-caption { color: var(--fg-soft); font-size: 11px; margin: 4px 0 2px;
  font-variant-numeric: tabular-nums; }
.chart-legend { color: var(--fg-soft); font-size: 12px; margin-top: 8px; }
.chart-legend .lg { margin-right: 10px; font-weight: 600; }
.chart-legend .lg-close { color: #4c9aff; }
.chart-legend .lg-ema   { color: #f5a623; }
.chart-legend .lg-s50   { color: #3ec46d; }
.chart-legend .lg-s200  { color: #e2574c; }
.chart-markers-legend { margin-top: 3px; }
.chart-legend .lg-asof   { color: #94a3b8; }
.chart-legend .lg-entry  { color: #9b59b6; }
.chart-legend .lg-stop   { color: #e2574c; }
.chart-legend .lg-target { color: #3ec46d; }
.chart-fallback { color: var(--fg-soft); padding: 1em; font-size: 13px; }
.chart-guide { margin-top: 8px; font-size: 12px; color: var(--fg-soft); }
.chart-guide > summary { cursor: pointer; color: var(--fg); font-weight: 600; }
.chart-guide > summary:hover { color: var(--accent); }
.chart-guide .cg-sec { margin: 8px 0 0; }
.chart-guide .cg-sec > b { color: var(--fg); }
.chart-guide ul { margin: 3px 0 6px; padding-left: 18px; }
.chart-guide li { margin: 1px 0; line-height: 1.55; }
.chart-guide .k { font-weight: 600; }
section.report-section { margin-top: 24px; }
section.report-section > h2 {
  font-size: 16px; margin: 0 0 10px; padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
pre.report {
  white-space: pre-wrap; word-wrap: break-word; font-family: inherit;
  font-size: 14px; line-height: 1.7; margin: 0; color: var(--fg);
  background: var(--card); padding: 16px; border: 1px solid var(--border);
  border-radius: 8px;
}
pre.report strong { color: var(--fg); }
pre.report h3, pre.report h4, pre.report h5, pre.report h6 {
  margin: 12px 0 4px; font-size: 14px;
}
/* Deep-link mark — index-page snippet click navigates here with
   #mark=<phrase>; JS below wraps the first occurrence and pulses. */
mark.snippet-target {
  background: rgba(245, 158, 11, 0.6); color: #ffffff;
  padding: 1px 4px; border-radius: 3px;
  box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.45);
  animation: markPulse 1.8s ease-out;
}
@keyframes markPulse {
  0%   { background: rgba(245, 158, 11, 0.95);
         box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.6); }
  100% { background: rgba(245, 158, 11, 0.6);
         box-shadow: 0 0 0 2px rgba(245, 158, 11, 0.45); }
}
/* ── Stock info header cards ─────────────────────────────── */
.si-header { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0 18px; }
.si-card {
  flex: 1 1 0; min-width: 130px; max-width: 240px;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px 16px; display: flex; flex-direction: column; gap: 2px;
}
.si-card .si-label { font-size: 11px; color: var(--fg-soft); }
.si-card .si-value { font-size: 18px; font-weight: 700; color: var(--fg);
  font-variant-numeric: tabular-nums; }
.si-card .si-sub { font-size: 11px; color: var(--fg-soft); }
/* ── Tab navigation ──────────────────────────────────────── */
.si-tabs { display: flex; gap: 0; border-bottom: 2px solid var(--border); margin: 0 0 16px; }
.si-tab {
  padding: 8px 16px; font-size: 13px; color: var(--fg-soft); cursor: pointer;
  border-bottom: 2px solid transparent; margin-bottom: -2px;
  font-family: inherit; background: none; border-top: none; border-left: none; border-right: none;
}
.si-tab:hover { color: var(--fg); }
.si-tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
.si-pane { display: none; }
.si-pane.active { display: block; }
/* ── Company info grid ───────────────────────────────────── */
.si-section { margin: 16px 0; }
.si-section-title {
  font-size: 15px; font-weight: 700; color: var(--fg);
  border-bottom: 1px solid var(--border); padding-bottom: 6px; margin-bottom: 10px;
}
.si-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 0;
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  background: var(--card);
}
.si-row {
  display: contents;
}
.si-key {
  padding: 8px 14px; font-size: 13px; color: var(--fg-soft);
  border-bottom: 1px solid var(--border); background: var(--bg);
}
.si-val {
  padding: 8px 14px; font-size: 13px; color: var(--fg); font-weight: 500;
  border-bottom: 1px solid var(--border);
}
.si-grid .si-row:last-child .si-key,
.si-grid .si-row:last-child .si-val { border-bottom: none; }
.si-desc {
  background: var(--card); border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 18px; font-size: 13px; line-height: 1.65; color: var(--fg);
  margin-bottom: 12px;
}
/* ── Consensus card ──────────────────────────────────────── */
.si-consensus { display: flex; flex-wrap: wrap; gap: 14px; margin: 12px 0; }
.si-con-badge {
  font-size: 16px; font-weight: 700; padding: 6px 18px;
  border-radius: 8px; color: #fff;
}
.si-con-badge.buy { background: #26a69a; }
.si-con-badge.hold { background: #f5a623; }
.si-con-badge.sell { background: #e2574c; }
.si-con-target { font-size: 14px; color: var(--fg); }
.si-con-target .pct { font-weight: 600; }
.si-range-bar { width: 100%; max-width: 380px; margin: 6px 0; }
.si-range-track {
  height: 6px; background: var(--border); border-radius: 3px; position: relative;
}
.si-range-fill {
  height: 100%; border-radius: 3px; position: absolute;
}
.si-range-marker {
  position: absolute; top: -4px; width: 3px; height: 14px;
  background: var(--fg); border-radius: 2px; transform: translateX(-50%);
}
.si-range-labels { display: flex; justify-content: space-between; font-size: 11px; color: var(--fg-soft); }
/* ── Earnings / Research / Holders tables ────────────────── */
.si-table {
  width: 100%; border-collapse: collapse; font-size: 13px;
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
.si-table thead { background: var(--bg); }
.si-table th {
  padding: 8px 12px; text-align: left; font-weight: 600; color: var(--fg-soft);
  border-bottom: 2px solid var(--border); font-size: 12px;
}
.si-table td {
  padding: 7px 12px; border-bottom: 1px solid var(--border); color: var(--fg);
}
.si-table tbody tr:last-child td { border-bottom: none; }
.si-table .num { text-align: right; font-variant-numeric: tabular-nums; }
.si-table .pos { color: #26a69a; font-weight: 600; }
.si-table .neg { color: #e2574c; font-weight: 600; }
/* ── News cards ──────────────────────────────────────────── */
.si-news-item {
  padding: 10px 0; border-bottom: 1px solid var(--border);
}
.si-news-item:last-child { border-bottom: none; }
.si-news-title { font-size: 13px; font-weight: 600; color: var(--fg); }
.si-news-title a { color: var(--fg); text-decoration: none; }
.si-news-title a:hover { color: var(--accent); }
.si-news-meta { font-size: 11px; color: var(--fg-soft); margin-top: 2px; }
.si-empty { color: var(--fg-soft); font-size: 13px; padding: 14px 0; }
.si-tl-item { display: flex; gap: 10px; padding: 10px 0; border-bottom: 1px solid var(--border); }
.si-tl-date { min-width: 80px; font-size: 12px; color: var(--fg-soft); flex-shrink: 0; }
.si-tl-body { flex: 1; }
.si-tl-title { font-size: 13px; }
.si-tl-title a { color: var(--fg); text-decoration: none; }
.si-tl-title a:hover { text-decoration: underline; }
.si-tl-meta { font-size: 11px; color: var(--fg-soft); margin-top: 2px; }
.si-tl-badge { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 3px; font-weight: 600; margin-left: 6px; vertical-align: middle; }
.si-tl-badge.high { background: #e2574c22; color: #e2574c; }
.si-tl-badge.medium { background: #ff980022; color: #ff9800; }
.si-tl-badge.low { background: #26a69a22; color: #26a69a; }
.si-tl-badge.info { background: #42a5f522; color: #42a5f5; }
.si-tl-type { display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 3px; background: var(--border); margin-right: 6px; }
@media (max-width: 560px) {
  .si-card { max-width: none; }
  .si-grid { grid-template-columns: 1fr; }
  .si-tab { padding: 6px 10px; font-size: 12px; }
}
"""


# Deep-link snippet scroll: when navigating from the index page's snippet
# panel, the URL carries #mark=<URL-encoded query>. This script walks the
# report-section text nodes, wraps the first occurrence with
# <mark.snippet-target>, scrolls it into view, and lets the CSS pulse run.
# Bails silently when the query no longer matches (analysis regenerated /
# stale link). Runs in DOMContentLoaded so the markdown render has settled.
_DETAIL_DEEP_LINK_JS = """
(function() {
  function run() {
    const m = (location.hash || '').match(/^#mark=(.+)$/);
    if (!m) return;
    let q;
    try { q = decodeURIComponent(m[1]); } catch (e) { return; }
    q = (q || '').trim();
    if (!q) return;
    const root = document.querySelector('.report-section + .report-section')
              || document.querySelector('.report-section')
              || document.body;
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node) {
        if (!node.parentNode) return NodeFilter.FILTER_REJECT;
        const tag = node.parentNode.tagName;
        if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'MARK') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent;
      const idx = text.toLowerCase().indexOf(ql);
      if (idx >= 0) {
        const parent = node.parentNode;
        const before = text.slice(0, idx);
        const match = text.slice(idx, idx + q.length);
        const after = text.slice(idx + q.length);
        if (before) parent.insertBefore(document.createTextNode(before), node);
        const mk = document.createElement('mark');
        mk.className = 'snippet-target';
        mk.textContent = match;
        parent.insertBefore(mk, node);
        if (after) parent.insertBefore(document.createTextNode(after), node);
        parent.removeChild(node);
        setTimeout(function() {
          mk.scrollIntoView({behavior: 'smooth', block: 'center'});
        }, 60);
        return;
      }
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
  // Also re-run on hash change so the user can re-trigger by clicking a
  // different snippet within the same tab via browser back/forward.
  window.addEventListener('hashchange', run);
})();
"""


# ── Price chart (lightweight-charts) ─────────────────────────────────────
# Self-hosted: _ensure_chart_lib() downloads the standalone bundle into
# ARCHIVE_ROOT once (VM has internet), so detail pages reference a local
# file — no per-page CDN dependency, works offline after the first fetch.
# Pinned to v4.2.0 (addLineSeries API; v5 renamed to addSeries).
_LWC_LIB_NAME = "lightweight-charts.standalone.production.js"
_LWC_CDN_URL = (
    "https://unpkg.com/lightweight-charts@4.2.0/dist/"
    "lightweight-charts.standalone.production.js"
)


def _ensure_chart_lib() -> bool:
    """Vendor the lightweight-charts standalone JS into ARCHIVE_ROOT once.
    Idempotent (skips when already present + non-trivial size). Returns
    True if the file is present afterwards. Non-fatal — a failure just
    means detail-page charts don't render (text stays intact)."""
    import urllib.request

    dest = ARCHIVE_ROOT / _LWC_LIB_NAME
    try:
        if dest.exists() and dest.stat().st_size > 10000:
            return True
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            _LWC_CDN_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 10000:
            log.warning(
                "dashboard: chart lib download too small (%d bytes) — skipping",
                len(data),
            )
            return dest.exists()
        tmp = dest.with_suffix(".js.tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        log.info("dashboard: chart lib vendored (%d bytes)", len(data))
        return True
    except Exception as exc:
        log.warning("dashboard: chart lib ensure failed: %s", exc)
        return dest.exists()


def _render_chart_section(rec: dict, analysis_markers: list[dict] | None = None) -> str:
    """Emit the price-chart <section> when the record carries a price_chart
    payload (schema v2+). The JSON rides in a <script type=application/json>
    block (not an attribute) to avoid escaping bloat; _CHART_JS reads it and
    renders client-side. Empty string for older v1 entries (graceful).

    `analysis_markers` (optional): past-analysis verdicts for this ticker,
    rendered as ▲매수/▼매도/●보유 markers on the timeline (track record)."""
    pc = rec.get("price_chart")
    if not isinstance(pc, dict) or not pc.get("times") or not pc.get("close"):
        return ""
    import json as _json

    # Parse entry/stop/target from the report text at render time (not
    # stored) — so parser improvements + backfilled old entries both pick
    # up markers. Plausibility-filtered vs the close series so a mis-parse
    # can't draw a misleading line.
    try:
        from bot.chart_data import parse_trade_levels
        markers = parse_trade_levels(
            rec.get("full_report") or "", pc.get("close") or []
        )
    except Exception:
        markers = {}
    pc = dict(pc)  # don't mutate the cached/loaded record
    if markers:
        pc["markers"] = markers
    # 시점가 = 분석일 종가 (stored series 의 마지막 종가). 차트 메인 라인은
    # 클라이언트가 /api/chart 로 '오늘까지(현재가)' 를 다시 받아 그리되,
    # 이 값은 분석 시점 기준선으로 항상 수평선 표시.
    _closes = [c for c in (pc.get("close") or []) if c is not None]
    if _closes:
        pc["as_of_close"] = _closes[-1]
    _times = pc.get("times") or []
    if _times:
        pc["as_of_date"] = _times[-1]
    # 과거 추천 마커 (이 종목의 분석 이력 — track record).
    if analysis_markers:
        pc["analysis_markers"] = analysis_markers

    # Defuse any stray '</' so the JSON can't terminate the <script> block.
    payload = _json.dumps(pc, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    tkr = _html.escape(rec.get("ticker", ""))
    return f"""
  <section class="report-section">
    <h2>📈 가격 차트</h2>
    <div class="chart-toolbar">
      <span class="chart-tf-group">
        <button class="chart-tf-btn" data-kind="interval" data-val="1d">일봉</button>
        <button class="chart-tf-btn" data-kind="interval" data-val="1wk">주봉</button>
        <button class="chart-tf-btn" data-kind="interval" data-val="1mo">월봉</button>
      </span>
      <span class="chart-tf-sep">·</span>
      <span class="chart-tf-group">
        <button class="chart-tf-btn" data-kind="range" data-val="1mo">1개월</button>
        <button class="chart-tf-btn" data-kind="range" data-val="3mo">3개월</button>
        <button class="chart-tf-btn" data-kind="range" data-val="6mo">6개월</button>
        <button class="chart-tf-btn" data-kind="range" data-val="ytd">YTD</button>
        <button class="chart-tf-btn" data-kind="range" data-val="1y">1년</button>
        <button class="chart-tf-btn" data-kind="range" data-val="3y">3년</button>
        <button class="chart-tf-btn" data-kind="range" data-val="5y">5년</button>
        <button class="chart-tf-btn" data-kind="range" data-val="max">전체</button>
      </span>
      <span class="chart-tf-status" id="chart-status"></span>
    </div>
    <div class="chart-toolbar chart-ind-toolbar">
      <span class="chart-ind-label">지표:</span>
      <button class="chart-ind-btn" data-ind="candle">캔들</button>
      <button class="chart-ind-btn" data-ind="ma">이평선</button>
      <button class="chart-ind-btn" data-ind="bb">볼린저</button>
      <button class="chart-ind-btn" data-ind="vol">거래량</button>
      <button class="chart-ind-btn" data-ind="rsi">RSI</button>
      <button class="chart-ind-btn" data-ind="macd">MACD</button>
      <button class="chart-ind-btn" data-ind="log">로그</button>
      <button class="chart-ind-btn" data-ind="events">공시</button>
    </div>
    <div class="chart-row">
      <div class="chart-main">
        <div id="chart-headline" class="chart-headline"></div>
        <div id="chart-ohlc" class="chart-ohlc"></div>
        <div id="price-chart" class="price-chart" data-ticker="{tkr}"></div>
        <div id="rsi-chart" class="sub-chart"></div>
        <div id="macd-chart" class="sub-chart"></div>
      </div>
      <div id="chart-values" class="chart-values"></div>
    </div>
    <div id="chart-caption" class="chart-caption"></div>
    <script type="application/json" id="chart-data">{payload}</script>
    <div id="chart-disc" class="chart-disc"></div>
    <div class="chart-legend">
      상단 헤드라인=현재가·기간수익률(절대+%)·거래량 · OHLC 바=날짜·시고저종·일간등락(마우스 올리면 그 봉, 떼면 마지막 봉) · 현재가=장중 라이브(KR은 KIS 실시간 우선, 그 외 yfinance ~15분 지연) · 시점가=분석일 종가 · 분석 후=시점가 대비 현재가 변동% · 기간=표시 구간 수익률(YTD=연초 이후 포함) · 진입/손절/목표=트레이드 플랜 · ▲매수/▼매도/●보유 마커=우리 과거 추천(+5거래일 결과) · ■ 작은 사각=공시(수주·소송 초록·시설투자 파랑·주주환원 보라·자본변동 주황·M&A 청록·리스크 빨강·최대주주변경 분홍, hover 시 차트 아래에 종류·제목·원문 링크) · 차트 아래 캡션=범위·봉 개수·날짜범위·출처 · 마우스 hover로 그 날 값 확인 · 지표 버튼으로 캔들/이평선/볼린저/거래량/RSI/MACD/로그/공시 on/off (새로고침 시 기본값으로 회귀)
    </div>
    <details class="chart-guide">
      <summary>ℹ️ 차트 보는 법 — 라인·지표·조작 자세히</summary>
      <div class="cg-sec"><b>가격 라인 / 기준선</b>
        <ul>
          <li><span class="k" style="color:#4c9aff">현재가</span> — 장중 라이브(KR은 KIS 실시간 우선, 그 외 yfinance ~15분 지연). 우측 축에 항상 표시. 이상 시세(분할·조정 오류 등)는 자동으로 걸러져 직전 종가로 대체.</li>
          <li><span class="k" style="color:#94a3b8">시점가</span>(회색 점선) — 분석한 날의 종가, 즉 "그때 가격" 기준선.</li>
          <li><span class="k" style="color:#9b59b6">진입</span> / <span class="k" style="color:#e2574c">손절</span> / <span class="k" style="color:#3ec46d">목표</span>(점선) — 트레이더 플랜의 가격대(본문에서 자동 추출, 비현실 값은 자동 제외).</li>
          <li>세로축(우측 가격) 라벨은 KRW(₩)는 <span class="k">만/억</span> 약식, 그 외 시장은 천단위 콤마로 — 큰 숫자 가독성.</li>
        </ul>
      </div>
      <div class="cg-sec"><b>상단 헤드라인 · OHLC 바</b>
        <ul>
          <li>차트 위 <span class="k">현재가</span>(크게) · <span class="k">기간 수익률</span>(표시 구간의 절대 변동 + %) · <span class="k">거래량</span> — 한눈에.</li>
          <li>그 아래 <span class="k">OHLC 바</span> — 날짜 · 시·고·저·종 · 일간 등락% · (이평선 켜면)10EMA/50SMA/200SMA. 차트에 마우스를 올리면 그 봉 값으로 바뀌고, 떼면 마지막 봉으로 돌아갑니다.</li>
        </ul>
      </div>
      <div class="cg-sec"><b>우측 값 패널 (지금 값 한눈에)</b>
        <ul>
          <li><span class="k">분석 후</span> — 시점가 대비 현재가 변동%. 분석 이후 우리 방향이 맞았는지(초록=올랐다·빨강=내렸다).</li>
          <li><span class="k">기간 N</span> — 지금 보이는 구간(1개월·3개월·6개월·YTD·1년·3년·5년·전체)의 수익률. 범위를 바꾸면 그 구간 기준으로 갱신. YTD=연초(1/1) 이후.</li>
          <li><span class="k">52주 신고가/신저가</span> — 최근 1년 최고·최저가(항상 표시, 차트 범위·지표 토글 무관).</li>
          <li>그 아래는 켜둔 지표의 최신값(이평선·볼린저·RSI·MACD·거래량). 가격 항목엔 통화 기호(₩/¥/$ 등) 표시.</li>
        </ul>
      </div>
      <div class="cg-sec"><b>과거 추천 마커 (우리 track record)</b>
        <ul>
          <li><span style="color:#26a69a">▲ 매수</span> · <span style="color:#e2574c">▼ 매도</span> · <span style="color:#94a3b8">● 보유</span> — 이 종목을 우리가 분석한 날 + 그날의 판정.</li>
          <li>마커 옆 <span class="k">+8.3%</span> 같은 수치 = 그 추천의 5거래일 뒤 실제 결과(성과 복기).</li>
        </ul>
      </div>
      <div class="cg-sec"><b>공시 마커 (날짜별 작은 사각 ■)</b>
        <ul>
          <li>핵심 공시만 날짜에 작게 표시 — <span style="color:#26a69a">수주·계약/소송</span> · <span style="color:#4c9aff">시설투자</span> · <span style="color:#b07cff">주주환원</span>(배당·자기주식) · <span style="color:#f5a623">자본변동</span>(증자·CB·감자) · <span style="color:#2dd4bf">M&A·지분</span>(합병·양수도) · <span style="color:#e2574c">리스크</span>(상장폐지·거래정지·횡령) · <span style="color:#f78fb3">최대주주변경</span>. (실적·임원·Reg FD 등 그 외는 제외.) 마커에 hover하면 <b>차트 아래 패널</b>에 그 날 공시의 종류·전체 제목·간단 설명·<b>원문 보기 링크</b> 표시.</li>
          <li>출처: KR DART · US SEC 8-K · JP EDINET · TW MOPS · CN/HK AKShare(무료). US 8-K 는 항목 종류가 넓어(실적·Reg FD·임원 위주) 매칭되는 마커가 KR DART(세밀한 공시명)보다 적게 표시될 수 있음. <b>호재/악재 판단은 안 함</b> — 종류만 색, 내용은 원문에서 직접 확인. '공시' 버튼으로 on/off(<b>기본 OFF</b> — 선택해서 보기).</li>
        </ul>
      </div>
      <div class="cg-sec"><b>보조지표 버튼</b> — 페이지 안에서 자유롭게 켜고 끌 수 있습니다. 새로고침하면 기본값(캔들·이평선·거래량·RSI)으로 돌아갑니다.
        <ul>
          <li><span class="k">캔들</span> — 라인 ↔ 캔들(시·고·저·종) 전환.</li>
          <li><span class="k">이평선</span> — <span style="color:#f5a623">10 EMA</span>(단기) · <span style="color:#3ec46d">50 SMA</span>(중기) · <span style="color:#e2574c">200 SMA</span>(장기) 추세선.</li>
          <li><span class="k" style="color:#7890c8">볼린저</span> — 20일·2σ 밴드. 상단 부근=과열, 하단 부근=과매도, 폭=변동성.</li>
          <li><span class="k">거래량</span> — 가격 아래 막대(상승 초록/하락 빨강).</li>
          <li><span class="k" style="color:#b07cff">RSI</span> — 하단 별도 패널. 70↑ 과열 · 30↓ 과매도(흔한 해석).</li>
          <li><span class="k" style="color:#4c9aff">MACD</span> — 하단 별도 패널. 라인이 시그널 위로 교차=상승 모멘텀, 아래로=하락.</li>
          <li><span class="k">로그</span> — 세로축 로그 스케일. 긴 기간 %변동 비교에 유리.</li>
          <li><span class="k">공시</span> — 공시 마커 표시/숨김(위 '공시 마커' 참고).</li>
        </ul>
      </div>
      <div class="cg-sec"><b>기간 · 봉 · 조작</b>
        <ul>
          <li><span class="k">일/주/월봉</span> + <span class="k">1개월·3개월·6개월·YTD·1년·3년·5년·전체</span> 범위. 주·월봉은 이평선·RSI 등이 그 단위로 재계산(일봉만 본문 TECHNICAL SNAPSHOT과 일치).</li>
          <li>마우스 <span class="k">hover</span> → 그 날짜의 모든 값 툴팁 + 상단 OHLC 바 갱신. 드래그=좌우 이동, 휠/핀치=확대·축소, 더블클릭=전체 보기.</li>
          <li>차트 아래 <span class="k">캡션</span> — 현재 범위·봉 종류·봉 개수·날짜 범위·데이터 출처.</li>
          <li>지표를 켜고 꺼도 보던 확대 구간은 그대로 유지됩니다.</li>
        </ul>
      </div>
    </details>
  </section>"""


_CHART_JS = """
(function(){
  var el = document.getElementById('price-chart');
  var dataEl = document.getElementById('chart-data');
  if (!el || !dataEl) return;
  var initial;
  try { initial = JSON.parse(dataEl.textContent); } catch (e) { return; }
  if (!initial || !initial.times || !initial.close) return;
  var ticker = el.getAttribute('data-ticker') || '';
  var markers = initial.markers || null;
  var asOfClose = (initial && initial.as_of_close != null) ? initial.as_of_close : null;
  var analysisMarkers = (initial && initial.analysis_markers) || null;
  var chart = null, rsiChart = null, macdChart = null;
  var curInterval = '1d', curRange = '1y';
  var lastData = null;   // 마지막 렌더 데이터 — 지표 토글 시 refetch 없이 재렌더
  var curSym = '';       // 현재 시장 통화 기호(₩/¥/€/NT$/HK$/$) — render 시 세팅

  // 지표 on/off 상태 (새로고침 시 항상 기본값으로 회귀 — 사용자 정책 2026-06-06).
  // 페이지 안에서는 토글 자유(in-memory ind 변경 후 재렌더), 단 localStorage 미저장.
  var IND_KEY = 'noah_chart_ind_v1';
  var IND_DEFAULT = { candle:true, ma:true, bb:false, vol:true, rsi:true, macd:false, log:false, events:false };
  function loadInd(){
    try { localStorage.removeItem(IND_KEY); } catch(e){}  // 옛 영속값 정리
    var out = {};
    for (var k in IND_DEFAULT) out[k] = IND_DEFAULT[k];
    return out;
  }
  function saveInd(){ /* no-op: 새로고침 시 기본값 회귀(미영속) */ }
  var ind = loadInd();

  function isDark(){ return document.documentElement.dataset.theme === 'dark'; }
  function txtColor(){ return isDark() ? '#cbd5e1' : '#334155'; }
  function gridColor(){ return isDark() ? 'rgba(148,163,184,0.12)' : 'rgba(100,116,139,0.12)'; }

  // 여러 차트(가격 + RSI + MACD) 시간축 동기화 — 한쪽 줌/팬이 전부 반영.
  function linkTimeScales(charts){
    var lock = false;
    charts.forEach(function(src){
      src.timeScale().subscribeVisibleLogicalRangeChange(function(r){
        if (lock || !r) return; lock = true;
        charts.forEach(function(dst){ if (dst !== src) { try { dst.timeScale().setVisibleLogicalRange(r); } catch(e){} } });
        lock = false;
      });
    });
  }
  function subChart(elem){
    return LightweightCharts.createChart(elem, {
      height: 120,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: txtColor(), fontFamily: 'inherit', attributionLogo: false },
      grid: { vertLines: { color: gridColor() }, horzLines: { color: gridColor() } },
      rightPriceScale: { borderVisible: false, minimumWidth: 130, scaleMargins: { top: 0.12, bottom: 0.12 } },
      // rightOffset 7: 메인 차트와 동일 여백 → RSI/MACD 도 우측 끝이 안 붙고 시간축 정렬 일치.
      timeScale: { borderVisible: false, timeVisible: false, visible: false, rightOffset: 7 },
      crosshair: { mode: 0 }
    });
  }
  function showSub(id, on){ var e = document.getElementById(id); if (e) { e.className = 'sub-chart' + (on ? '' : ' hidden'); e.innerHTML = ''; } return on ? document.getElementById(id) : null; }

  function render(d, preserve){
    if (typeof LightweightCharts === 'undefined') {
      el.innerHTML = '<div class="chart-fallback">차트 라이브러리를 불러오지 못했습니다 (텍스트 분석은 아래 그대로).</div>';
      return;
    }
    if (!d || !d.times || !d.close) return;
    lastData = d;
    curSym = d.currency || '';
    // 지표 토글 재렌더(preserve)면 현재 줌/팬을 복원하려고 먼저 캡처.
    var prevRange = null;
    if (chart) { try { prevRange = chart.timeScale().getVisibleLogicalRange(); } catch(e){} }
    if (chart) { try { chart.remove(); } catch(e){} chart = null; }
    if (rsiChart) { try { rsiChart.remove(); } catch(e){} rsiChart = null; }
    if (macdChart) { try { macdChart.remove(); } catch(e){} macdChart = null; }
    el.innerHTML = '';
    var layout = { background: { type: 'solid', color: 'transparent' }, textColor: txtColor(), fontFamily: 'inherit', attributionLogo: false };
    var grid = { vertLines: { color: gridColor() }, horzLines: { color: gridColor() } };
    chart = LightweightCharts.createChart(el, {
      height: 480,
      layout: layout, grid: grid,
      // 축/priceLine/크로스헤어 라벨 포매터 — raw '1716000' 대신 만/억(KRW)·
      // 콤마(그 외), 하단 마진 외삽으로 생기는 음수 가격 라벨(-250000) 숨김.
      localization: { priceFormatter: fmtAxis },
      // mode 1 = 로그 스케일 (긴 기간 뷰에서 % 변동 비교 가능). 기본 0=선형.
      // bottom 0.20: 가격 영역을 키워 디테일↑ (거래량 오버레이 top:0.82 와 비중첩).
      // minimumWidth 130: 현재가/시점가 라벨(이름+값)이 가격축 바깥 여백에 들어가
      // 캔들 위로 안 넘치게(사용자 2026-06-05 — 태그는 차트 밖 오른쪽).
      rightPriceScale: { borderVisible: false, minimumWidth: 130, mode: ind.log ? 1 : 0, scaleMargins: { top: 0.06, bottom: 0.20 } },
      // rightOffset 7: 우측 끝에 여백 → 최근 캔들·마커가 가장자리/가격축에 안 붙고
      // 빈 공간에 놓임(사용자 가독성 2026-06-05).
      timeScale: { borderVisible: false, timeVisible: false, rightOffset: 7 },
      crosshair: { mode: 1 }
    });
    function zip(a){ var o=[]; if(!a) return o; for(var i=0;i<d.times.length;i++){ var v=a[i]; if(v===null||v===undefined) continue; o.push({ time: d.times[i], value: v }); } return o; }
    var prec = (d.decimals === 0) ? 0 : 2;
    var pf = { precision: prec, minMove: (prec === 0) ? 1 : 0.01 };
    var hasOHLC = d.open && d.high && d.low;
    var mainS;
    // 장중 last_price(~15분 지연) 가 있으면 series 마지막 봉을 라이브 값으로
    // 대체 → '현재가' 라벨이 D-1 종가 대신 장중 라이브를 가리킨다. MA 는
    // 별도 사전 계산이라 흔들리지 않음 (시각적 마지막 1점만 갱신).
    var lp = (d.last_price != null) ? d.last_price : null;
    if (ind.candle && hasOHLC) {
      // 캔들 — OHLC 가 있을 때만.
      mainS = chart.addCandlestickSeries({ upColor:'#26a69a', downColor:'#e2574c', borderUpColor:'#26a69a', borderDownColor:'#e2574c', wickUpColor:'#26a69a', wickDownColor:'#e2574c', priceFormat: pf, lastValueVisible: true, priceLineVisible: false, title: '현재가' });
      var cd = [];
      for (var i = 0; i < d.times.length; i++) {
        if (d.open[i]==null||d.high[i]==null||d.low[i]==null||d.close[i]==null) continue;
        cd.push({ time: d.times[i], open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] });
      }
      if (lp != null && cd.length > 0) {
        var lb = cd[cd.length - 1];
        // 마지막 봉의 close 만 라이브로 교체 (high/low 는 보존; 라이브가
        // high·low 를 벗어나면 자동 보정).
        lb.close = lp;
        if (lp > lb.high) lb.high = lp;
        if (lp < lb.low)  lb.low  = lp;
      }
      mainS.setData(cd);
    } else {
      mainS = chart.addLineSeries({ color: '#4c9aff', lineWidth: 2, priceFormat: pf, lastValueVisible: true, priceLineVisible: false, title: '현재가' });
      var closeData = zip(d.close);
      if (lp != null && closeData.length > 0) {
        var last = closeData[closeData.length - 1];
        closeData[closeData.length - 1] = { time: last.time, value: lp };
      }
      mainS.setData(closeData);
    }
    // 마커 = 과거 추천(▲매수/▼매도/●보유 + 5거래일 결과) + 공시 이벤트(작은 사각,
    // 종류별 색, hover로 제목). 둘을 한 배열로 모아 시간순 정렬 후 한 번에 setMarkers.
    var firstT = d.times[0], lastT = d.times[d.times.length - 1];
    var mk = [];
    if (analysisMarkers && analysisMarkers.length) {
      for (var ai = 0; ai < analysisMarkers.length; ai++) {
        var a = analysisMarkers[ai];
        if (!a.time || a.time < firstT || a.time > lastT) continue;
        var up = a.dir === 'up', dn = a.dir === 'down';
        // 마커 텍스트는 결과%만(화살표가 매수/매도/보유 방향 표시 → 긴 'Overweight' 단어
        // 생략해 캔들 가림 최소화). 미해소(ret 없음)면 화살표만(텍스트 없음).
        var txt = (a.ret != null ? (a.ret >= 0 ? '+' : '') + a.ret.toFixed(1) + '%' : '');
        mk.push({
          time: a.time,
          position: up ? 'belowBar' : (dn ? 'aboveBar' : 'inBar'),
          color: up ? '#26a69a' : (dn ? '#e2574c' : '#94a3b8'),
          shape: up ? 'arrowUp' : (dn ? 'arrowDown' : 'circle'),
          text: txt, size: 2
        });
      }
    }
    if (ind.events && d.events && d.events.length) {
      for (var ei = 0; ei < d.events.length; ei++) {
        var ev = d.events[ei];
        if (!ev.time || ev.time < firstT || ev.time > lastT) continue;
        mk.push({ time: ev.time, position: 'belowBar', color: ev.color || '#94a3b8', shape: 'square', size: 2 });
      }
    }
    if (mk.length) {
      mk.sort(function(a, b){ return a.time < b.time ? -1 : (a.time > b.time ? 1 : 0); });
      try { mainS.setMarkers(mk); } catch (e) {}
    }
    if (ind.ma) {
      if (d.ema10)  chart.addLineSeries({ color: '#f5a623', lineWidth: 1, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.ema10));
      if (d.sma50)  chart.addLineSeries({ color: '#3ec46d', lineWidth: 1, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.sma50));
      if (d.sma200) chart.addLineSeries({ color: '#e2574c', lineWidth: 1, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.sma200));
    }
    if (ind.bb && d.bb_u) {
      // 볼린저밴드(20,2σ) — 상/하 점선 + 중심선.
      chart.addLineSeries({ color: 'rgba(120,144,200,0.9)', lineWidth: 1, lineStyle: 2, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.bb_u));
      chart.addLineSeries({ color: 'rgba(120,144,200,0.5)', lineWidth: 1, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.bb_m));
      chart.addLineSeries({ color: 'rgba(120,144,200,0.9)', lineWidth: 1, lineStyle: 2, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.bb_l));
    }
    if (ind.vol && d.volume) {
      var volS = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      var vd = [], prevC = null;
      for (var i = 0; i < d.times.length; i++) {
        var vv = d.volume[i]; var c = d.close[i];
        if (vv !== null && vv !== undefined) {
          var up = (prevC === null || c === null || c === undefined) ? true : (c >= prevC);
          vd.push({ time: d.times[i], value: vv, color: up ? 'rgba(76,175,80,0.45)' : 'rgba(229,87,76,0.45)' });
        }
        if (c !== null && c !== undefined) prevC = c;
      }
      volS.setData(vd);
    }
    function priceLine(p, color, title, style, showLabel) {
      if (p === null || p === undefined) return;
      // 가독성(사용자 2026-06-05): 우측 끝 축 라벨 박스는 '현재가' 하나만(라인
      // 마지막값). 시점가·진입·손절·목표는 점선만(값은 우측 패널) → 현재가≈시점가
      // 일 때 박스끼리 겹쳐 최근 캔들을 가리던 문제 해소.
      mainS.createPriceLine({ price: p, color: color, lineWidth: 1, lineStyle: (style===undefined?2:style), axisLabelVisible: !!showLabel, title: title });
    }
    priceLine(asOfClose, '#94a3b8', '시점가', 2, true);
    if (markers) {
      priceLine(markers.entry,  '#9b59b6', '진입', 2, false);
      priceLine(markers.stop,   '#e2574c', '손절', 2, false);
      priceLine(markers.target, '#3ec46d', '목표', 2, false);
    }
    if (!(preserve && prevRange)) chart.timeScale().fitContent();
    chart.applyOptions({ width: el.clientWidth });

    // 공시 내용 패널(차트 아래) — 마커 hover 시 종류·전체 제목·설명·원문 링크.
    // 차트 안 floating 툴팁은 좁아 제목 잘림 → 아래 고정 패널에 표시(사용자 2026-06-05).
    var discEl = document.getElementById('chart-disc');
    function escTt(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
    var DISC_TYPE = {
      order:       { l:'수주·계약', c:'#26a69a', d:'단일판매·공급계약 — 매출 가시성' },
      litigation:  { l:'소송',     c:'#26a69a', d:'소송·제소·피소 등 법적 분쟁' },
      capex:       { l:'시설투자', c:'#4c9aff', d:'신규 시설/설비 투자 — 증설·생산능력 확대' },
      shareholder: { l:'주주환원', c:'#b07cff', d:'배당·자기주식 취득/소각 — 주주환원' },
      capital:     { l:'자본변동', c:'#f5a623', d:'증자·전환사채·감자 등 — 주식수/자본 변동(희석 가능)' },
      mna:         { l:'M&A·지분', c:'#2dd4bf', d:'합병·영업양수도·타법인주식 취득/처분 — 구조 변경' },
      risk:        { l:'리스크',   c:'#e2574c', d:'상장폐지·거래정지·감사의견거절·횡령배임 등 — 중대 악재' },
      control:     { l:'최대주주변경', c:'#f78fb3', d:'최대주주·경영권 변경 — 지배구조 변화' },
      other:       { l:'공시',     c:'#94a3b8', d:'기타 공시' }
    };
    var DISC_HINT = '<span class="cd-empty">📋 공시 마커(■)에 마우스를 올리면 그 날 공시의 종류·제목·설명·원문 링크가 여기 표시됩니다.</span>';
    function showDisc(time){
      if (!discEl || !(ind.events && d.events && d.events.length)) return;
      var evs = d.events.filter(function(e){ return e.time === time; });
      if (!evs.length) return;  // 공시 없는 날 hover는 직전 표시 유지(아래로 읽으러 가도 안 사라짐)
      var html = '';
      for (var i = 0; i < evs.length && i < 8; i++){
        var e = evs[i], t = DISC_TYPE[e.type] || DISC_TYPE.other;
        var link = e.url ? ' · <a href="' + escTt(e.url) + '" target="_blank" rel="noopener">원문 →</a>' : '';
        // 구조화 요약(DART 숫자)이 있으면 그걸, 없으면 종류 설명을 표시.
        var desc = e.summary ? escTt(e.summary) : t.d;
        html += '<div><span style="color:' + t.c + '">●</span> <b>' + escTt(e.time) + '</b> [' + t.l + '] ' +
                escTt(e.title) + '<span class="cd-desc"> — ' + desc + '</span>' + link + '</div>';
      }
      if (evs.length > 8) html += '<div class="cd-empty">+' + (evs.length - 8) + '건 더</div>';
      discEl.innerHTML = html;
    }
    if (discEl && !discEl.innerHTML) discEl.innerHTML = DISC_HINT;

    // 크로스헤어 hover 툴팁 — 커서 지점의 날짜 + 활성 지표 값(OHLC/종가/이평선/
    // 볼린저/RSI/MACD/거래량). 가격 행은 통화 기호 prefix(fmtPrice).
    var tip = document.createElement('div');
    tip.className = 'chart-tooltip';
    tip.style.display = 'none';
    el.appendChild(tip);
    chart.subscribeCrosshairMove(function(param){
      if (!param || !param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tip.style.display = 'none'; buildOHLC(d, null); return;   // 커서 이탈 → 마지막 봉 복귀
      }
      var idx = d.times.indexOf(param.time);
      if (idx < 0) { tip.style.display = 'none'; buildOHLC(d, null); return; }
      buildOHLC(d, idx);   // 상단바를 hover 봉으로 갱신
      var rows = ['<div class="tt-date">' + param.time + '</div>'];
      function trow(name, val, color, dc, kind) {
        if (val === null || val === undefined) return;
        var s = (kind === 'vol') ? fmtVol(val) : (kind === 'raw' ? fmtNum(val, dc) : fmtPrice(val, dc));
        rows.push('<div><span style="color:' + color + '">' + name + '</span> ' + s + '</div>');
      }
      if (ind.candle && hasOHLC) {
        trow('시가', d.open && d.open[idx], '#94a3b8', prec);
        trow('고가', d.high && d.high[idx], '#26a69a', prec);
        trow('저가', d.low && d.low[idx], '#e2574c', prec);
      }
      trow('종가', d.close[idx], '#4c9aff', prec);
      if (ind.ma) {
        trow('10 EMA', d.ema10 && d.ema10[idx], '#f5a623', prec);
        trow('50 SMA', d.sma50 && d.sma50[idx], '#3ec46d', prec);
        trow('200 SMA', d.sma200 && d.sma200[idx], '#e2574c', prec);
      }
      if (ind.bb && d.bb_u) {
        trow('볼린저상', d.bb_u && d.bb_u[idx], '#7890c8', prec);
        trow('볼린저하', d.bb_l && d.bb_l[idx], '#7890c8', prec);
      }
      if (ind.rsi && d.rsi) trow('RSI', d.rsi[idx], '#b07cff', 1, 'raw');
      if (ind.macd && d.macd) trow('MACD', d.macd[idx], '#4c9aff', Math.max(prec, 3), 'raw');
      if (ind.vol && d.volume) trow('거래량', d.volume[idx], '#94a3b8', 0, 'vol');
      // 공시 내용은 floating 툴팁이 아니라 차트 아래 패널(#chart-disc)에 표시.
      showDisc(param.time);
      tip.innerHTML = rows.join('');
      tip.style.display = 'block';
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var x = param.point.x + 14, y = param.point.y + 14;
      if (x + tw > el.clientWidth) x = param.point.x - tw - 14;
      if (y + th > 440) y = param.point.y - th - 14;
      if (x < 0) x = 2; if (y < 0) y = 2;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });

    var linked = [chart];

    // RSI(14) pane.
    var rsiEl = showSub('rsi-chart', ind.rsi && !!d.rsi);
    if (rsiEl) {
      rsiChart = subChart(rsiEl);
      var rsiS = rsiChart.addLineSeries({ color: '#b07cff', lineWidth: 1, priceFormat: { precision: 1, minMove: 0.1 }, lastValueVisible: true, priceLineVisible: false, title: 'RSI' });
      rsiS.setData(zip(d.rsi));
      rsiS.createPriceLine({ price: 70, color: 'rgba(229,87,76,0.55)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '70' });
      rsiS.createPriceLine({ price: 30, color: 'rgba(76,175,80,0.55)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '30' });
      rsiChart.timeScale().fitContent();
      rsiChart.applyOptions({ width: rsiEl.clientWidth });
      linked.push(rsiChart);
    }

    // MACD(12,26,9) pane — line + signal + histogram + zero.
    var macdEl = showSub('macd-chart', ind.macd && !!d.macd);
    if (macdEl) {
      macdChart = subChart(macdEl);
      var mpf = { precision: 3, minMove: 0.001 };
      var histS = macdChart.addHistogramSeries({ priceFormat: mpf, lastValueVisible: false, priceLineVisible: false });
      var hd = [];
      for (var i = 0; i < d.times.length; i++) {
        var hv = d.macd_hist[i];
        if (hv !== null && hv !== undefined) hd.push({ time: d.times[i], value: hv, color: hv >= 0 ? 'rgba(38,166,154,0.6)' : 'rgba(229,87,76,0.6)' });
      }
      histS.setData(hd);
      macdChart.addLineSeries({ color: '#4c9aff', lineWidth: 1, priceFormat: mpf, lastValueVisible: true, priceLineVisible: false, title: 'MACD' }).setData(zip(d.macd));
      macdChart.addLineSeries({ color: '#f5a623', lineWidth: 1, priceFormat: mpf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.macd_signal));
      macdChart.timeScale().fitContent();
      macdChart.applyOptions({ width: macdEl.clientWidth });
      linked.push(macdChart);
    }

    if (linked.length > 1) linkTimeScales(linked);
    // 토글 재렌더면 저장한 뷰 복원 — linkTimeScales 가 sub-pane 까지 동기화.
    if (preserve && prevRange) { try { chart.timeScale().setVisibleLogicalRange(prevRange); } catch(e){} }
    buildValues(d);
    buildHeadline(d);
    buildOHLC(d, null);   // 기본 = 마지막 봉
    buildCaption(d);
  }

  function lastNonNull(a){ if(!a) return null; for(var i=a.length-1;i>=0;i--){ if(a[i]!==null&&a[i]!==undefined) return a[i]; } return null; }
  function firstNonNull(a){ if(!a) return null; for(var i=0;i<a.length;i++){ if(a[i]!==null&&a[i]!==undefined) return a[i]; } return null; }
  function fmtNum(v, decimals){
    if (v === null || v === undefined) return '–';
    try { return Number(v).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals }); }
    catch(e){ return '' + v; }
  }
  // 차트 축/priceLine/크로스헤어 가격 라벨 포매터 (메인 차트만; RSI/MACD 는
  // 자체 format). priceFormat(precision)만으론 raw '1716000' 이라 가독성이
  // 낮았고, 가격 축이 하단 마진으로 외삽되며 음수 격자에 '-250000' 라벨이
  // 붙던 버그가 있었음. v<0 은 숨기고, KRW(₩) 큰 값은 만/억 약식, 그 외
  // 시장은 천단위 콤마(+적정 소수).
  function fmtAxis(v){
    if (v === null || v === undefined || isNaN(v)) return '';
    if (v < 0) return '';
    if (curSym === '₩' && v >= 10000) {
      if (v >= 1e8) { var eok = v / 1e8; return (eok >= 100 ? Math.round(eok) : Math.round(eok * 10) / 10) + '억'; }
      var man = v / 1e4;
      return (man >= 1000 ? Math.round(man).toLocaleString('en-US') : Math.round(man * 10) / 10) + '만';
    }
    var dec = (lastData && lastData.decimals === 0) ? 0 : 2;
    return fmtNum(v, dec);
  }
  // 가격 행엔 시장 통화 기호(₩/¥/€/NT$/HK$/$) prefix — payload 가 늘 계산해
  // 보내던 d.currency 를 실제로 표시 (이전엔 맨숫자라 시장 구분 불가).
  function fmtPrice(v, decimals){
    if (v === null || v === undefined) return '–';
    return curSym + fmtNum(v, decimals);
  }
  // 거래량은 K/M/B 약식 (풀정수는 너무 길어 패널/툴팁을 밀어냄).
  function fmtVol(v){
    if (v === null || v === undefined) return '–';
    var n = Number(v); if (isNaN(n)) return '' + v;
    var a = Math.abs(n);
    if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
    return fmtNum(n, 0);
  }
  // 범위/봉 한국어 라벨 — 헤드라인·기간수익률·캡션에서 공통 사용.
  function rangeLabel(r){ r = r || curRange; return ({'1mo':'1개월','3mo':'3개월','6mo':'6개월','ytd':'YTD','1y':'1년','3y':'3년','5y':'5년','max':'전체'})[r] || r; }
  function intervalLabel(i){ i = i || curInterval; return ({'1d':'일봉','1wk':'주봉','1mo':'월봉'})[i] || i; }
  function buildValues(d){
    var vEl = document.getElementById('chart-values');
    if (!vEl) return;
    var dec = (d.decimals === 0) ? 0 : 2;
    var items = [];
    // 분석 가격 — 차트 안에선 가까우면 라벨이 겹쳐 가려지므로 패널에서 항상
    // 보이게 (선 색과 매칭). 현재가/시점가/진입/손절/목표.
    items.push(['현재가', (d.last_price != null ? d.last_price : lastNonNull(d.close)), '#4c9aff', dec]);
    if (asOfClose != null) items.push(['시점가', asOfClose, '#94a3b8', dec]);
    // 분석 후 변동 — 시점가(분석일 종가) 대비 현재가 %. 이 차트의 핵심
    // 복기 지표 (그때 가격 ↔ 지금 가격). 현재가는 패널 '현재가' 와 동일값.
    if (asOfClose != null && asOfClose > 0) {
      var _cur = (d.last_price != null ? d.last_price : lastNonNull(d.close));
      if (_cur != null) {
        var _chg = (_cur - asOfClose) / asOfClose * 100;
        var _col = _chg > 0 ? '#26a69a' : (_chg < 0 ? '#e2574c' : '#94a3b8');
        items.push(['분석 후', (_chg >= 0 ? '+' : '') + _chg.toFixed(1) + '%', _col, null, 'pct']);
      }
    }
    if (markers) {
      if (markers.entry  != null) items.push(['진입', markers.entry,  '#9b59b6', dec]);
      if (markers.stop   != null) items.push(['손절', markers.stop,   '#e2574c', dec]);
      if (markers.target != null) items.push(['목표', markers.target, '#3ec46d', dec]);
    }
    // 기간 수익률 — 로드된 범위(1개월~전체)의 first bar → 현재가 %. 범위/봉
    // 전환 시 그 윈도 기준으로 갱신. 분석 관련 값이 아니라 차트 윈도 통계
    // 이므로 진입/손절/목표(분석 값) 아래에 배치.
    var _first = firstNonNull(d.close);
    var _curp = (d.last_price != null ? d.last_price : lastNonNull(d.close));
    if (_first != null && _first > 0 && _curp != null) {
      var _pchg = (_curp - _first) / _first * 100;
      var _pcol = _pchg > 0 ? '#26a69a' : (_pchg < 0 ? '#e2574c' : '#94a3b8');
      items.push(['기간 ' + rangeLabel(curRange), (_pchg >= 0 ? '+' : '') + _pchg.toFixed(1) + '%', _pcol, null, 'pct']);
    }
    // 52주 신고가/신저가 — 항상 표시. 기본 그룹의 맨 밑(진입/손절/목표가 있으면 그 아래).
    if (d.wk52_high != null) items.push(['52주 신고가', d.wk52_high, '#26a69a', dec]);
    if (d.wk52_low  != null) items.push(['52주 신저가', d.wk52_low,  '#e2574c', dec]);
    items.push(null);   // 구분선
    if (ind.ma) {
      if (d.ema10)  items.push(['10 EMA', lastNonNull(d.ema10), '#f5a623', dec]);
      if (d.sma50)  items.push(['50 SMA', lastNonNull(d.sma50), '#3ec46d', dec]);
      if (d.sma200) items.push(['200 SMA', lastNonNull(d.sma200), '#e2574c', dec]);
    }
    if (ind.bb && d.bb_u) {
      items.push(['볼린저상', lastNonNull(d.bb_u), '#7890c8', dec]);
      items.push(['볼린저하', lastNonNull(d.bb_l), '#7890c8', dec]);
    }
    if (ind.rsi && d.rsi)   items.push(['RSI', lastNonNull(d.rsi), '#b07cff', 1, 'raw']);
    if (ind.macd && d.macd) items.push(['MACD', lastNonNull(d.macd), '#4c9aff', Math.max(dec,3), 'raw']);
    if (ind.vol && d.volume) items.push(['거래량', lastNonNull(d.volume), '#94a3b8', 0, 'vol']);
    var html = '';
    for (var i = 0; i < items.length; i++) {
      if (items[i] === null) { html += '<div class="cv-sep"></div>'; continue; }
      var it = items[i], kind = it[4];
      var sval = (kind === 'pct') ? it[1]
               : (kind === 'vol') ? fmtVol(it[1])
               : (kind === 'raw') ? fmtNum(it[1], it[3])
               : fmtPrice(it[1], it[3]);
      html += '<div class="cv-item"><span class="cv-dot" style="background:' + it[2] + '"></span>'
            + '<span class="cv-name">' + it[0] + '</span>'
            + '<span class="cv-val">' + sval + '</span></div>';
    }
    vEl.innerHTML = html;
  }

  // 상단 헤드라인 — 현재가(large) + 기간 수익률(절대+%) + 거래량. 레퍼런스
  // 터미널 패턴(최근 종가 / 기간 수익률 / 거래량 상단 노출). 현재가는 값패널과
  // 동일 소스(라이브 우선), 기간 수익률은 표시 구간의 first→현재가.
  function buildHeadline(d){
    var hEl = document.getElementById('chart-headline');
    if (!hEl) return;
    var dec = (d.decimals === 0) ? 0 : 2;
    var cur = (d.last_price != null) ? d.last_price : lastNonNull(d.close);
    var first = firstNonNull(d.close);
    var html = '<span class="ch-cur">' + fmtPrice(cur, dec) + '</span>';
    if (first != null && first > 0 && cur != null) {
      var abs = cur - first, pct = abs / first * 100;
      var col = abs > 0 ? '#26a69a' : (abs < 0 ? '#e2574c' : '#94a3b8');
      var sign = abs >= 0 ? '+' : '';
      html += '<span class="ch-chg" style="color:' + col + '">' + sign + fmtNum(abs, dec)
            + ' (' + sign + pct.toFixed(2) + '%) · ' + rangeLabel(curRange) + '</span>';
    }
    var vol = lastNonNull(d.volume);
    if (vol != null) html += '<span class="ch-vol">거래량 ' + fmtVol(vol) + '</span>';
    hEl.innerHTML = html;
  }

  // OHLC 상단바 — 마지막 봉(기본) 또는 크로스헤어 hover 봉의 날짜·시고저종·
  // 일간 등락·활성 이평선. 레퍼런스의 '날짜 시 X 고 Y 저 Z 종 W MA..' 라인 미러.
  function buildOHLC(d, idx){
    var oEl = document.getElementById('chart-ohlc');
    if (!oEl || !d.times || !d.times.length) return;
    if (idx == null || idx < 0 || idx >= d.times.length) idx = d.times.length - 1;
    var dec = (d.decimals === 0) ? 0 : 2;
    var hasO = d.open && d.high && d.low;
    var parts = ['<span class="co-date">' + (d.times[idx] || '') + '</span>'];
    function seg(name, val, color){
      if (val === null || val === undefined) return;
      parts.push('<span class="co-seg"><span style="color:' + color + '">' + name + '</span> '
               + fmtPrice(val, dec) + '</span>');
    }
    if (hasO) {
      seg('시', d.open[idx], '#94a3b8');
      seg('고', d.high[idx], '#26a69a');
      seg('저', d.low[idx], '#e2574c');
    }
    // 종가 — 마지막 봉이면 라이브 현재가 반영(차트 렌더와 일치).
    var cval = d.close[idx];
    if (idx === d.times.length - 1 && d.last_price != null) cval = d.last_price;
    seg('종', cval, '#4c9aff');
    // 일간 등락 — 종가 vs 직전 봉 종가.
    if (idx > 0 && d.close[idx - 1] != null && cval != null && d.close[idx - 1] > 0) {
      var ch = (cval - d.close[idx - 1]) / d.close[idx - 1] * 100;
      var col = ch > 0 ? '#26a69a' : (ch < 0 ? '#e2574c' : '#94a3b8');
      parts.push('<span class="co-seg" style="color:' + col + '">' + (ch >= 0 ? '+' : '') + ch.toFixed(2) + '%</span>');
    }
    if (ind.ma) {
      if (d.ema10 && d.ema10[idx] != null)  seg('10EMA', d.ema10[idx], '#f5a623');
      if (d.sma50 && d.sma50[idx] != null)  seg('50SMA', d.sma50[idx], '#3ec46d');
      if (d.sma200 && d.sma200[idx] != null) seg('200SMA', d.sma200[idx], '#e2574c');
    }
    oEl.innerHTML = parts.join('');
  }

  // 차트 하단 캡션 — 범위·봉종류·봉 개수·날짜범위·출처(레퍼런스 하단 미러).
  function buildCaption(d){
    var cEl = document.getElementById('chart-caption');
    if (!cEl) return;
    var n = (d.times && d.times.length) ? d.times.length : 0;
    var first = n ? d.times[0] : '', last = n ? d.times[n - 1] : '';
    cEl.textContent = rangeLabel(curRange) + ' · ' + intervalLabel(curInterval) + ' · '
                    + n + '개 봉 · ' + first + ' → ' + last + ' · Yahoo Finance';
  }

  function setActive(){
    var btns = document.querySelectorAll('.chart-tf-btn');
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i];
      var on = (b.getAttribute('data-kind') === 'interval' && b.getAttribute('data-val') === curInterval)
            || (b.getAttribute('data-kind') === 'range' && b.getAttribute('data-val') === curRange);
      if (on) b.classList.add('active'); else b.classList.remove('active');
    }
  }
  function setActiveInd(){
    var btns = document.querySelectorAll('.chart-ind-btn');
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i]; var k = b.getAttribute('data-ind');
      if (ind[k]) b.classList.add('active'); else b.classList.remove('active');
    }
  }

  function status(msg){ var st = document.getElementById('chart-status'); if (st) st.textContent = msg || ''; }

  function load(){
    setActive();
    status('⏳ 불러오는 중…');
    el.style.opacity = '0.5';
    fetch('../api/chart?ticker=' + encodeURIComponent(ticker) + '&interval=' + curInterval + '&range=' + curRange,
          { headers: { 'Accept': 'application/json' } })
      .then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function(j){
        el.style.opacity = '';
        if (j && j.ok && j.chart) { status(''); render(j.chart); }
        else { status('⚠️ 데이터 없음 (' + ((j && j.error) || 'no data') + ')'); }
      })
      .catch(function(err){
        el.style.opacity = '';
        var m = (err && err.message) ? err.message : 'error';
        status('⚠️ 현재가 불러오기 실패 (' + m + ') — 분석일까지 표시 중');
      });
  }

  function onClick(e){
    var t = e.target;
    while (t && t !== document && t.classList && !t.classList.contains('chart-tf-btn') && !t.classList.contains('chart-ind-btn')) t = t.parentNode;
    if (!t || t === document || !t.classList) return;
    if (t.classList.contains('chart-ind-btn')) {
      var k = t.getAttribute('data-ind');
      if (k in ind) { ind[k] = !ind[k]; saveInd(); setActiveInd(); if (lastData) render(lastData, true); }
      return;
    }
    if (t.classList.contains('chart-tf-btn')) {
      var kind = t.getAttribute('data-kind'), val = t.getAttribute('data-val');
      if (kind === 'interval') curInterval = val;
      else if (kind === 'range') curRange = val;
      load();
    }
  }

  function start(){
    document.addEventListener('click', onClick);
    setActiveInd();
    render(initial);   // 즉시 placeholder (분석일까지) — 깜빡임 방지
    setActive();
    load();            // 곧바로 '오늘까지(현재가)' 를 받아 교체
    window.addEventListener('resize', function(){
      if (chart) chart.applyOptions({ width: el.clientWidth });
      var re = document.getElementById('rsi-chart'); if (rsiChart && re) rsiChart.applyOptions({ width: re.clientWidth });
      var me = document.getElementById('macd-chart'); if (macdChart && me) macdChart.applyOptions({ width: me.clientWidth });
    });
    var last = document.documentElement.dataset.theme;
    setInterval(function(){
      var th = document.documentElement.dataset.theme;
      if (th !== last) {
        last = th;
        var opts = { layout: { textColor: txtColor() }, grid: { vertLines: { color: gridColor() }, horzLines: { color: gridColor() } } };
        if (chart) chart.applyOptions(opts);
        if (rsiChart) rsiChart.applyOptions(opts);
        if (macdChart) macdChart.applyOptions(opts);
      }
    }, 60000);
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', start); }
  else { start(); }
})();
"""


def _fmt_num(v, decimals=0, prefix="", suffix=""):
    """Format a number with commas. Return '—' for None/0."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n == 0:
        return "—"
    if decimals == 0:
        return f"{prefix}{int(n):,}{suffix}"
    return f"{prefix}{n:,.{decimals}f}{suffix}"


def _fmt_mcap(v, currency_sym="", currency=""):
    """Format market cap in human-readable units (조/억/兆/億/B/M/T)."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if currency_sym in ("₩", "원") or currency == "KRW":
        if n >= 1e12:
            return f"₩{n / 1e12:,.2f}조"
        if n >= 1e8:
            return f"₩{n / 1e8:,.0f}억"
        return f"₩{n:,.0f}"
    if currency in ("JPY", "TWD", "HKD", "CNY"):
        sym = {"JPY": "¥", "TWD": "NT$", "HKD": "HK$", "CNY": "¥"}.get(currency, currency_sym or "$")
        t_label, y_label = ("兆", "億")
        if n >= 1e12:
            return f"{sym}{n / 1e12:,.2f}{t_label}"
        if n >= 1e8:
            return f"{sym}{n / 1e8:,.0f}{y_label}"
        return f"{sym}{n:,.0f}"
    if currency_sym == "¥":
        if n >= 1e12:
            return f"¥{n / 1e12:,.2f}兆"
        if n >= 1e8:
            return f"¥{n / 1e8:,.0f}億"
        return f"¥{n:,.0f}"
    sym = currency_sym or "$"
    if n >= 1e12:
        return f"{sym}{n / 1e12:,.2f}T"
    if n >= 1e9:
        return f"{sym}{n / 1e9:,.2f}B"
    if n >= 1e6:
        return f"{sym}{n / 1e6:,.1f}M"
    return f"{sym}{n:,.0f}"


def _fmt_shares(v):
    """Format shares outstanding in M/B."""
    if v is None:
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    if n >= 1e9:
        return f"{n / 1e9:,.3f}B"
    if n >= 1e6:
        return f"{n / 1e6:,.1f}M"
    return f"{n:,.0f}"


def _currency_sym(currency: str) -> str:
    return {"KRW": "₩", "JPY": "¥", "TWD": "NT$", "HKD": "HK$",
            "CNY": "¥", "EUR": "€", "GBP": "£"}.get(currency or "", "$")


_FIN_ITEM_KR: dict[str, str] = {
    "Total Revenue": "매출액", "Operating Revenue": "영업수익",
    "Cost Of Revenue": "매출원가", "Gross Profit": "매출총이익",
    "Operating Income": "영업이익", "Operating Expense": "영업비용",
    "EBITDA": "EBITDA", "EBIT": "EBIT",
    "Net Income": "당기순이익", "Basic EPS": "기본 EPS", "Diluted EPS": "희석 EPS",
    "Tax Provision": "법인세", "Interest Expense": "이자비용",
    "Total Assets": "자산총계", "Current Assets": "유동자산",
    "Cash And Cash Equivalents": "현금 및 현금성자산",
    "Total Liabilities Net Minority Interest": "부채총계",
    "Current Liabilities": "유동부채", "Total Debt": "총차입금",
    "Stockholders Equity": "자본총계", "Retained Earnings": "이익잉여금",
    "Common Stock Equity": "보통주 자본", "Working Capital": "운전자본",
    "Operating Cash Flow": "영업활동현금흐름", "Capital Expenditure": "자본적지출",
    "Free Cash Flow": "잉여현금흐름", "Investing Cash Flow": "투자활동현금흐름",
    "Financing Cash Flow": "재무활동현금흐름", "End Cash Position": "기말현금",
    "Repurchase Of Capital Stock": "자사주 매입", "Cash Dividends Paid": "배당금 지급",
    "Net Income From Continuing Operations": "계속사업이익",
    "Reconciled Depreciation": "감가상각비", "Total Expenses": "총비용",
    "Selling General And Administration": "판관비",
    "Research And Development": "연구개발비",
    "Total Non Current Assets": "비유동자산",
    "Total Non Current Liabilities Net Minority Interest": "비유동부채",
    "Net Tangible Assets": "순유형자산", "Tangible Book Value": "유형자산 장부가",
    "Invested Capital": "투하자본", "Total Capitalization": "총자본",
    "Share Issued": "발행주식수", "Ordinary Shares Number": "보통주 수",
    "Changes In Cash": "현금 변동", "Beginning Cash Position": "기초현금",
    "Issuance Of Debt": "차입금 조달", "Repayment Of Debt": "차입금 상환",
    "Issuance Of Capital Stock": "주식 발행",
    "Depreciation And Amortization": "감가상각비",
    "Change In Working Capital": "운전자본 변동",
    "Stock Based Compensation": "주식보상비용",
    "Deferred Tax": "이연법인세",
    "Accounts Receivable": "매출채권", "Accounts Payable": "매입채무",
    "Inventory": "재고자산", "Goodwill": "영업권",
    "Other Intangible Assets": "기타무형자산",
    "Long Term Debt": "장기차입금", "Short Long Term Debt": "단기차입금",
    "Net PPE": "순유형자산(PPE)",
    "Gross PPE": "유형자산(PPE) 총액",
    "Land And Improvements": "토지", "Buildings And Improvements": "건물",
    "Machinery Furniture Equipment": "기계장치",
    "Accumulated Depreciation": "감가상각누계액",
    "Other Current Assets": "기타유동자산", "Other Non Current Assets": "기타비유동자산",
    "Other Current Liabilities": "기타유동부채",
    "Other Non Current Liabilities": "기타비유동부채",
    "Minority Interest": "비지배지분",
    "Interest Income": "이자수익",
    "Pretax Income": "세전이익", "Tax Effect Of Unusual Items": "특별항목 세효과",
    "Total Unusual Items": "특별항목 합계",
    "Total Revenue (KRW)": "매출액 (₩)", "Net Income (KRW)": "당기순이익 (₩)",
}


def diagnose_detail_sources(ticker: str) -> dict:
    """Probe every news / research / consensus source for ``ticker`` and
    report, per source, whether it's reachable and how many rows it
    returns — so an operator can open
    ``/api/quote?ticker=X&debug=1`` in the browser (the dashboard is
    auth-gated) and get a DEFINITIVE answer to "is an empty tab our code,
    a missing key, or a blocked/unsupported source?" — without ssh.

    Never raises. Reports env-key PRESENCE as booleans only (never the
    values). ₩0 — same free clients the overlay uses.
    """
    import os as _os
    out: dict = {"ticker": ticker, "market": None, "env": {}, "sources": {}}
    tkr = (ticker or "").upper()

    def _probe(name: str, fn) -> None:
        try:
            res = fn()
            out["sources"][name] = res
        except Exception as exc:
            out["sources"][name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    if tkr.endswith((".KS", ".KQ")):
        out["market"] = "KR"
        out["env"] = {
            "NAVER_CLIENT_ID": bool(_os.getenv("NAVER_CLIENT_ID", "").strip()),
            "NAVER_CLIENT_SECRET": bool(_os.getenv("NAVER_CLIENT_SECRET", "").strip()),
            "DART_API_KEY": bool(_os.getenv("DART_API_KEY", "").strip()),
        }
        code = tkr.split(".")[0]

        def _dart_name():
            from bot.dart_client import get_dart
            dart = get_dart()
            if not dart:
                return {"ok": False, "error": "DART client unavailable (key missing?)"}
            nm = dart.stock_code_to_name(code)
            ci = dart.get_company_info(code) or {}
            return {"ok": bool(nm or ci.get("corp_name")),
                    "stock_code_to_name": nm,
                    "company_info_corp_name": ci.get("corp_name")}
        _probe("dart_name", _dart_name)

        def _http_status(url, headers=None):
            import requests
            try:
                r = requests.get(url, headers=headers or {}, timeout=12)
                return {"status": r.status_code, "bytes": len(r.text)}
            except Exception as exc:
                return {"status": None, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

        def _naver():
            from bot.naver_news_client import fetch_news, _API_URL
            from bot.dart_client import get_dart
            dart = get_dart()
            q = (dart.news_search_name(code) if dart else None) or tkr
            eng = dart.stock_code_to_name(code) if dart else None
            items = fetch_news(q, days_back=28, max_items=10)
            # 0 items with a valid Korean query + present keys means the API
            # itself is rejecting us — capture the raw status + Naver's error
            # body (keys live in headers, never the body, so this is safe).
            raw = {}
            if not items:
                import requests as _rq
                cid = _os.getenv("NAVER_CLIENT_ID", "").strip()
                csec = _os.getenv("NAVER_CLIENT_SECRET", "").strip()
                try:
                    rr = _rq.get(_API_URL,
                                 params={"query": q, "display": 5, "sort": "date"},
                                 headers={"X-Naver-Client-Id": cid,
                                          "X-Naver-Client-Secret": csec},
                                 timeout=12)
                    raw = {"status": rr.status_code,
                           "total": (rr.json().get("total") if rr.status_code == 200 else None),
                           "body": rr.text[:300]}
                except Exception as exc:
                    raw = {"error": f"{type(exc).__name__}: {str(exc)[:150]}"}
            return {"ok": bool(items), "query": q,
                    "english_registration": eng,
                    "count": len(items) if items else 0,
                    "sample": (items[0].get("title") if items else None),
                    "raw_api": raw}
        _probe("naver_news", _naver)

        def _gnews():
            from bot.google_news_client import fetch_news as gf
            from bot.dart_client import get_dart
            dart = get_dart()
            q = (dart.news_search_name(code) if dart else None) or tkr
            items = gf(q, days_back=28, max_items=10, lang="ko", country="KR", ceid="KR:ko")
            return {"ok": bool(items), "query": q,
                    "count": len(items) if items else 0,
                    "sample": (items[0].get("title") if items else None)}
        _probe("google_news_fallback", _gnews)

        def _hk():
            from bot.hk_consensus_client import (
                fetch_consensus, _fetch_list_html, _cell_texts)
            import re as _re
            r = fetch_consensus(ticker)
            # Dump a couple of raw <tr> cell-lists from the live list page so
            # the tolerant parser can be verified / refined against the real
            # markup without ssh.
            sample_rows = []
            try:
                html = _fetch_list_html(code) or ""
                for row_html in _re.findall(r"<tr[^>]*>(.*?)</tr>", html,
                                            _re.DOTALL | _re.I):
                    cells = _cell_texts(row_html)
                    if any(_re.search(r"\d{4}[-./]\d{2}[-./]\d{2}", c) for c in cells):
                        sample_rows.append(cells)
                    if len(sample_rows) >= 3:
                        break
            except Exception as exc:
                sample_rows = [f"dump error: {type(exc).__name__}: {str(exc)[:120]}"]
            return {"ok": bool(r and r.get("reports")),
                    "reports": len(r.get("reports", [])) if r else 0,
                    "target_price": (r or {}).get("target_price"),
                    "rating": (r or {}).get("rating"),
                    "sample_rows": sample_rows}
        _probe("hankyung_research", _hk)

        def _fng():
            import requests as _rq, re as _re
            url = (f"https://comp.fnguide.com/SVO2/asp/SVD_Main.asp"
                   f"?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701")
            url_cons = (f"https://comp.fnguide.com/SVO2/asp/SVD_Consensus.asp"
                        f"?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=108&stkGb=701")
            snip = {}
            for label, u in (("consensus", url_cons), ("main", url)):
                try:
                    rr = _rq.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
                    html = rr.text
                    # Snippet around the first 목표주가 label so we can see the
                    # actual markup the parser must match.
                    idx = html.find("목표주가")
                    if idx < 0:
                        idx = html.find("Consensus")
                    around = (html[max(0, idx - 40):idx + 220] if idx >= 0 else "")
                    around = _re.sub(r"\s+", " ", around)
                    snip[label] = {"status": rr.status_code, "bytes": len(html),
                                   "label_found": idx >= 0, "around": around[:260]}
                except Exception as exc:
                    snip[label] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            from bot.fnguide_consensus import fetch_consensus as fg
            r = fg(code)
            return {"ok": bool(r and r.get("target_mean")),
                    "target_mean": (r or {}).get("target_mean"),
                    "snippets": snip}
        _probe("fnguide_consensus", _fng)

    elif tkr.endswith(".T"):
        out["market"] = "JP"
        def _kb():
            from bot.kabutan_news import fetch_news
            items = fetch_news(code := tkr.split(".")[0], days_back=28, max_items=10)
            return {"ok": bool(items), "count": len(items) if items else 0}
        _probe("kabutan_news", _kb)
        def _kbc():
            from bot.kabutan_consensus import fetch_consensus as kc
            r = kc(ticker)
            return {"ok": bool(r and r.get("target_mean")), "target_mean": (r or {}).get("target_mean")}
        _probe("kabutan_consensus", _kbc)

    elif tkr.endswith((".TW", ".TWO")):
        out["market"] = "TW"
        def _cn():
            from bot.cnyes_client import fetch_news
            items = fetch_news(tkr.split(".")[0], days_back=28, max_items=10)
            return {"ok": bool(items), "count": len(items) if items else 0}
        _probe("cnyes_news", _cn)
        def _cnc():
            from bot.cnyes_consensus import fetch_consensus as cc
            r = cc(ticker)
            return {"ok": bool(r and r.get("target_mean")), "target_mean": (r or {}).get("target_mean")}
        _probe("cnyes_consensus", _cnc)

    elif tkr.endswith((".SS", ".SZ", ".BJ", ".HK")):
        out["market"] = "CN_HK"
        def _ak():
            from bot.akshare_client import get_akshare
            ak = get_akshare()
            if not ak:
                return {"ok": False, "error": "AKShare unavailable"}
            items = ak.fetch_news(ticker, days_back=28, max_items=10)
            return {"ok": bool(items), "count": len(items) if items else 0}
        _probe("akshare_news", _ak)

    else:
        out["market"] = "US"
        def _yf():
            import yfinance as yf
            n = yf.Ticker(ticker).news or []
            return {"ok": bool(n), "count": len(n)}
        _probe("yfinance_news", _yf)

    return out


def _load_stored_stock_info(ticker: str) -> dict | None:
    """Return the ``stock_info`` from the most recent archived analysis of
    ``ticker``, or None. Used as a fallback for the live FULL overlay when a
    fresh ``collect_stock_snapshot`` fails (e.g. yfinance .info rate-limit in
    the long-running dashboard process) — we'd rather render the last good
    snapshot than blank the heavy tabs."""
    try:
        tkr = (ticker or "").upper()
        best_date = ""
        best_si = None
        if not ARCHIVE_ROOT.exists():
            return None
        for day_dir in ARCHIVE_ROOT.iterdir():
            if not day_dir.is_dir() or not _DATE_RE.match(day_dir.name):
                continue
            jf = day_dir / f"{tkr}.json"
            if not jf.exists():
                continue
            try:
                rec = json.loads(jf.read_text(encoding="utf-8"))
            except Exception:
                continue
            si = rec.get("stock_info")
            if si and day_dir.name > best_date:
                best_date = day_dir.name
                best_si = si
        # Return a deep copy so the in-place enrichment below never mutates
        # the file-backed record the rest of the page renders from.
        return json.loads(json.dumps(best_si)) if best_si else None
    except Exception as exc:
        log.debug("_load_stored_stock_info failed for %s: %s", ticker, exc)
        return None


def _safe_dy_pct(dy_raw, div_rate=None, price=None):
    """Return dividend yield as display percentage (0–100), or None.

    yfinance ``dividendYield`` is unreliable: sometimes a decimal ratio
    (0.0044 = 0.44 %), sometimes already a percentage (1.44 = 1.44 %),
    sometimes just wrong (0.35 for a stock with real yield 0.44 %).
    Prefer computing from ``dividendRate / price`` when both exist.
    Cap at 20 % — no legitimate stock exceeds that.
    """
    # Method 1: compute from rate and price (most reliable)
    if div_rate and price and isinstance(div_rate, (int, float)) and isinstance(price, (int, float)) and price > 0:
        computed = (div_rate / price) * 100
        if 0 < computed <= 20:
            return round(computed, 2)
    # Method 2: interpret raw dividendYield with heuristic + sanity cap
    if dy_raw and isinstance(dy_raw, (int, float)) and dy_raw > 0:
        pct = dy_raw * 100 if dy_raw < 1.0 else dy_raw
        if 0 < pct <= 20:
            return round(pct, 2)
    return None


def _ensure_detail_enrichment(ticker: str, si: dict) -> None:
    """Fill the news / research / consensus tabs in-place when they're
    missing from ``si``. These come from our own market clients (Naver /
    한경 / Kabutan / cnyes / AKShare / EDGAR) — no yfinance, no LLM, ₩0 —
    so they stay reliable even when yfinance is throttled, and they
    back-fill analyses created before the enrichment code shipped.

    Mirrors archive.backfill_detail_tabs but operates on a single live
    snapshot. Each block independently try/excepted — any failure leaves
    that tab as-is rather than sinking the whole overlay."""
    if not si:
        return
    tkr = (ticker or "").upper()

    # ① News fallback (all markets)
    if not si.get("news"):
        try:
            from bot.stock_snapshot import _collect_news_fallback
            _collect_news_fallback(ticker, si)
        except Exception as exc:
            log.debug("_ensure_detail_enrichment: news %s: %s", ticker, exc)

    # ② KR research reports + consensus (Naver Finance primary → 한경 fallback)
    if tkr.endswith((".KS", ".KQ")):
        kr = si.get("kr", {})
        if not kr.get("research_reports"):
            _kr_research = None
            try:
                from bot.naver_research_client import fetch_research
                _kr_research = fetch_research(ticker)
            except Exception:
                pass
            # Fallback to 한경 if Naver returned nothing OR all reports
            # lack rating+target (detail page regex failure)
            _naver_hollow = (_kr_research and _kr_research.get("reports")
                             and not any(r.get("target") or r.get("rating")
                                         for r in _kr_research["reports"]))
            if not (_kr_research and _kr_research.get("reports")) or _naver_hollow:
                try:
                    from bot.hk_consensus_client import fetch_consensus as hk_fetch
                    _hk = hk_fetch(ticker)
                except Exception:
                    _hk = None
                if _hk and _hk.get("reports"):
                    if _naver_hollow:
                        # Naver had dates/brokers, 한경 has target/rating →
                        # merge: enrich Naver rows from 한경 by date+broker
                        _hk_map = {(r.get("date"), r.get("broker")): r
                                   for r in _hk["reports"]}
                        for r in _kr_research["reports"]:
                            match = _hk_map.get((r.get("date"), r.get("broker")))
                            if match:
                                if not r.get("target") and match.get("target"):
                                    r["target"] = match["target"]
                                if not r.get("rating") and match.get("rating"):
                                    r["rating"] = match["rating"]
                        # If still hollow after merge, use 한경 directly
                        if not any(r.get("target") or r.get("rating")
                                   for r in _kr_research["reports"]):
                            _kr_research = _hk
                    else:
                        _kr_research = _hk
            if _kr_research and _kr_research.get("reports"):
                si.setdefault("kr", {})["research_reports"] = _kr_research["reports"]
                if not si.get("target_mean") and not kr.get("consensus") and _kr_research.get("target_price"):
                    src = "Naver Finance"
                    si.setdefault("kr", {})["consensus"] = {
                        "source": src,
                        "target_mean": _kr_research["target_price"],
                        "rating": _kr_research.get("rating"),
                        "n_analysts": _kr_research.get("analyst_count"),
                        "last_report_date": _kr_research.get("last_report_date"),
                    }

    # ③ TW consensus (鉅亨網) + monthly revenue + PER/PBR
    elif tkr.endswith((".TW", ".TWO")):
        tw = si.get("tw", {})
        if not si.get("target_mean") and not tw.get("consensus"):
            try:
                from bot.cnyes_consensus import fetch_consensus as cnyes_fetch
                cn = cnyes_fetch(ticker)
                if cn and cn.get("target_mean"):
                    si.setdefault("tw", {})["consensus"] = {
                        "source": "鉅亨網",
                        "target_mean": cn["target_mean"],
                        "rating": cn.get("rating"),
                        "n_analysts": cn.get("n_analysts"),
                        "last_report_date": cn.get("last_report_date"),
                    }
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: TW consensus %s: %s", ticker, exc)
        # TW monthly revenue (FinMind/TWSE)
        if not tw.get("monthly_revenue"):
            try:
                from bot.finmind_client import fetch_monthly_revenue
                rev = fetch_monthly_revenue(ticker)
                if rev:
                    si.setdefault("tw", {})["monthly_revenue"] = rev
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: TW monthly revenue %s: %s", ticker, exc)
        # TW PER/PBR (FinMind/TWSE)
        if not tw.get("per_pbr"):
            try:
                from bot.finmind_client import fetch_per_pbr
                ppb = fetch_per_pbr(ticker)
                if ppb:
                    si.setdefault("tw", {})["per_pbr"] = ppb
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: TW PER/PBR %s: %s", ticker, exc)

    # ④ JP consensus (Kabutan)
    elif tkr.endswith(".T"):
        jp = si.get("jp", {})
        if not si.get("target_mean") and not jp.get("consensus"):
            try:
                from bot.kabutan_consensus import fetch_consensus as kb_fetch
                kb = kb_fetch(ticker)
                if kb and kb.get("target_mean"):
                    si.setdefault("jp", {})["consensus"] = {
                        "source": "Kabutan",
                        "target_mean": kb["target_mean"],
                        "rating": kb.get("rating"),
                        "n_analysts": kb.get("n_analysts"),
                        "last_report_date": kb.get("last_report_date"),
                    }
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: JP consensus %s: %s", ticker, exc)

    # ⑤ Financial statements (재무제표 tab — yfinance)
    if not si.get("financials"):
        try:
            import yfinance as yf
            from bot.stock_snapshot import _collect_financials
            _collect_financials(yf.Ticker(ticker), si)
        except Exception as exc:
            log.debug("_ensure_detail_enrichment: financials %s: %s", ticker, exc)

    # ⑥ Peer comparables (동종비교 tab — yfinance)
    if not si.get("peer_comps"):
        try:
            import yfinance as yf
            from bot.stock_snapshot import _collect_peer_multiples
            t = yf.Ticker(ticker)
            info = t.info or {}
            if info.get("industry"):
                _collect_peer_multiples(ticker, info, si)
        except Exception as exc:
            log.debug("_ensure_detail_enrichment: peer_comps %s: %s", ticker, exc)

    # ⑦ KR flow data (수급 tab — KIS + pykrx)
    if tkr.endswith((".KS", ".KQ")):
        kr = si.setdefault("kr", {})
        if not kr.get("flow"):
            try:
                from bot.kis_client import KisClient
                kis = KisClient()
                if kis._ready():
                    flow_data: dict = {}
                    inv = kis.get_investor_flow(ticker)
                    if inv:
                        flow_data["investor_flow"] = inv
                    credit = kis.get_credit_short_balance(ticker)
                    if credit:
                        flow_data["credit"] = credit
                    short = kis.get_short_sale(ticker)
                    if short:
                        flow_data["short_sale"] = short
                    program = kis.get_program_trade(ticker)
                    if program:
                        flow_data["program"] = program
                    if flow_data:
                        kr["flow"] = flow_data
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: KR flow %s: %s", ticker, exc)
            try:
                from bot.pykrx_client import (
                    get_kr_foreign_ownership_trend,
                    get_kr_short_balance_trend,
                )
                fo = get_kr_foreign_ownership_trend(ticker, days_back=30)
                if fo:
                    kr.setdefault("flow", {})["foreign_ownership"] = fo
                sb = get_kr_short_balance_trend(ticker, days_back=30)
                if sb:
                    kr.setdefault("flow", {})["short_trend"] = sb
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: pykrx trends %s: %s", ticker, exc)

    # ⑧ Disclosures (공시 tab — market-specific, 1년치 무료 API)
    if tkr.endswith((".KS", ".KQ")):
        kr = si.setdefault("kr", {})
        if not kr.get("disclosures"):
            try:
                from bot.dart_client import get_dart
                dart = get_dart()
                if dart:
                    stock_code = ticker.split(".")[0]
                    disclosures = dart.get_recent_disclosures(stock_code, days_back=365, limit=50)
                    if disclosures:
                        kr["disclosures"] = disclosures
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: DART disclosures %s: %s", ticker, exc)
    elif tkr.endswith(".T"):
        jp = si.setdefault("jp", {})
        if not jp.get("disclosures"):
            try:
                from bot.edinet_client import get_recent_disclosures as edinet_disc
                disc = edinet_disc(ticker, days_back=180, limit=30)
                if disc:
                    jp["disclosures"] = disc
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: EDINET disclosures %s: %s", ticker, exc)
    elif tkr.endswith((".TW", ".TWO")):
        tw = si.setdefault("tw", {})
        if not tw.get("disclosures"):
            try:
                from bot.mops_client import get_recent_disclosures as mops_disc
                disc = mops_disc(ticker, days_back=365, limit=50)
                if disc:
                    tw["disclosures"] = disc
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: MOPS disclosures %s: %s", ticker, exc)
    elif tkr.endswith((".SS", ".SZ", ".BJ", ".HK")):
        cn = si.setdefault("cn", {})
        if not cn.get("disclosures"):
            try:
                from bot.akshare_client import get_recent_disclosures as ak_disc
                disc = ak_disc(ticker, days_back=365, limit=50)
                if disc:
                    cn["disclosures"] = disc
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: AKShare disclosures %s: %s", ticker, exc)
    else:
        us = si.setdefault("us", {})
        if not us.get("disclosures"):
            try:
                from bot.edgar_client import get_recent_8k
                filings = get_recent_8k(ticker, days=365, top_n=30)
                if filings:
                    disc_rows = []
                    for f in filings:
                        labels = f.get("items_labels", [])
                        title = " / ".join(labels) if labels else f.get("items_raw", "8-K")
                        disc_rows.append({
                            "date": f.get("date", ""),
                            "title": title,
                            "url": f.get("url", ""),
                            "reporter": "SEC 8-K",
                        })
                    us["disclosures"] = disc_rows
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: EDGAR disclosures %s: %s", ticker, exc)


def build_live_quote(ticker: str, full: bool = False) -> dict | None:
    """Fresh live-quote payload for the detail-page overlay (numbers only).

    LIGHT (full=False): one yfinance ``.info`` call → price-derived
    multiples + consensus + 52주 + 이평. KR (.KS/.KQ) takes a KIS-first
    real-time 현재가 / PER / PBR / 시가총액 / 52주 override — KIS is the
    official KRX feed, fresher and more accurate than yfinance's often
    EOD-stale KR quotes. ~0.5-1s, intraday-fresh, ₩0.

    FULL (full=True): re-runs ``collect_stock_snapshot`` (every source —
    재무제표 / 실적 / 주주 / 수급 / 공시) and re-renders the heavy table
    panes so filing- / daily-cadence data reflects the latest filing.
    ₩0 (``set_cache_only`` → no Gemini call on the live path). The server
    caches this longer because the underlying data is slow-moving.

    Numbers only — never triggers Gemini translation. Graceful: returns
    None on any failure and the caller keeps the stored snapshot, so the
    page never blanks.
    """
    if full:
        # Re-snapshot from every source, re-render the heavy panes, hand
        # back ready HTML the client swaps in. set_cache_only keeps it ₩0.
        try:
            from bot.translate import set_cache_only
            set_cache_only(True)
        except Exception:
            pass
        # 1) Fresh full snapshot. yfinance .info is flaky / rate-limit-prone
        #    in the long-running dashboard process; a failure there must NOT
        #    sink the news / research / consensus tabs (those come from
        #    DART / Naver / 한경 / Kabutan / cnyes — no yfinance dependency).
        fresh = None
        try:
            from bot.stock_snapshot import collect_stock_snapshot
            fresh = collect_stock_snapshot(ticker)
        except Exception as exc:
            log.warning("build_live_quote: full snapshot failed for %s: %s", ticker, exc)
        # 2) Fall back to the stored snapshot when yfinance bailed, so the
        #    heavy panes still render real data (financials / holders / etc.).
        if not fresh:
            fresh = _load_stored_stock_info(ticker)
        if not fresh:
            return None
        # 3) Ensure the slow-moving tabs are filled even when the snapshot
        #    (stored or fresh) predates the news/research enrichment — old
        #    analyses created before that code shipped have empty tabs that
        #    only the live overlay can populate. Free (no LLM / no yfinance).
        try:
            _ensure_detail_enrichment(ticker, fresh)
        except Exception as exc:
            log.debug("build_live_quote: enrichment skipped for %s: %s", ticker, exc)
        try:
            parts = _render_stock_info_html({"ticker": ticker, "stock_info": fresh})
        except Exception as exc:
            log.warning("build_live_quote: full render failed for %s: %s", ticker, exc)
            return None
        panes = (parts or {}).get("panes", {}) or {}
        heavy = {k: v for k, v in panes.items() if v and k in (
            "si-financials", "si-earnings", "si-research", "si-holders",
            "si-flow", "si-disclosures", "si-risk", "si-peers", "si-news",
        )}
        if not heavy:
            return None
        _kst = datetime.timezone(datetime.timedelta(hours=9))
        return {"mode": "full", "panes": heavy,
                "meta": {"ts": datetime.datetime.now(_kst).strftime("%H:%M")}}

    # ── LIGHT — price-derived numbers, one .info call (+ KR KIS) ───────
    try:
        import yfinance as yf
    except Exception:
        return None
    is_kr = ticker.upper().endswith((".KS", ".KQ"))
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        log.debug("build_live_quote: yfinance .info failed for %s: %s", ticker, exc)
        info = {}
    if not info and not is_kr:
        return None

    currency = info.get("currency") or ("KRW" if is_kr else "USD")
    csym = _currency_sym(currency)
    _0dec = ("KRW", "JPY", "TWD", "HKD")
    pdec = 0 if currency in _0dec else 2

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    mcap = info.get("marketCap")
    vals = {
        "trailingPE": info.get("trailingPE"),
        "forwardPE": info.get("forwardPE"),
        "priceToBook": info.get("priceToBook"),
        "priceToSalesTrailing12Months": info.get("priceToSalesTrailing12Months"),
        "enterpriseToEbitda": info.get("enterpriseToEbitda"),
        "trailingEps": info.get("trailingEps"),
        "forwardEps": info.get("forwardEps"),
        "bookValue": info.get("bookValue"),
        "dividendYield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
        "fiftyDayAverage": info.get("fiftyDayAverage"),
        "twoHundredDayAverage": info.get("twoHundredDayAverage"),
    }
    target_mean = info.get("targetMeanPrice")
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    source, delayed = "yfinance", True

    if is_kr:
        # KIS is the official KRX feed — fresher / more accurate than
        # yfinance's frequently EOD-stale KR quotes. Realtime (2-min
        # cache) for 현재가; inquire-price for PER / PBR / EPS / BPS /
        # 시가총액 / 52주. Any miss falls through to the yfinance values.
        try:
            from bot.kis_client import get_kis
            kc = get_kis()
            cur = kc.get_current_price(ticker) or {}
            rt = kc.get_realtime_price(ticker)
            kis_price = rt or cur.get("price")
            if kis_price:
                price = kis_price
                source, delayed = "KIS 실시간", False
            for src_k, dst_k in (("per", "trailingPE"), ("pbr", "priceToBook"),
                                 ("eps", "trailingEps"), ("bps", "bookValue")):
                v = cur.get(src_k)
                if v:  # KIS sends 0 for unknown → keep yfinance value
                    vals[dst_k] = v
            if cur.get("market_cap"):
                mcap = cur["market_cap"]
            if cur.get("high_52w"):
                vals["fiftyTwoWeekHigh"] = cur["high_52w"]
            if cur.get("low_52w"):
                vals["fiftyTwoWeekLow"] = cur["low_52w"]
        except Exception as exc:
            log.debug("build_live_quote: KIS override skipped for %s: %s", ticker, exc)

    # Pre-format every field with the SAME helpers the renderer uses so
    # the client just drops strings into [data-q] cells (no JS-side
    # formatting drift). Skip None so a live miss never blanks a value
    # the stored snapshot already had.
    fmt: dict = {}
    if price:
        fmt["price"] = _fmt_num(price, decimals=pdec, prefix=csym)
    if mcap:
        fmt["mcap"] = _fmt_mcap(mcap, csym, currency)
    for k, suf in (("trailingPE", "x"), ("forwardPE", "x"), ("priceToBook", "x"),
                   ("priceToSalesTrailing12Months", "x"),
                   ("enterpriseToEbitda", "x"), ("beta", "")):
        v = vals.get(k)
        if isinstance(v, (int, float)):
            fmt[k] = f"{float(v):.2f}{suf}"
    _lq_dy = _safe_dy_pct(vals.get("dividendYield"), info.get("dividendRate"), price)
    if _lq_dy is not None:
        fmt["dividendYield"] = f"{_lq_dy:.2f}%"
    for k in ("trailingEps", "forwardEps", "bookValue",
              "fiftyDayAverage", "twoHundredDayAverage"):
        v = vals.get(k)
        if isinstance(v, (int, float)):
            fmt[k] = f"{float(v):,.2f}"

    pos: dict = {}
    w52h = vals.get("fiftyTwoWeekHigh")
    w52l = vals.get("fiftyTwoWeekLow")
    if price and w52h and w52l and w52h > w52l:
        wp = max(0.0, min(100.0, (price - w52l) / (w52h - w52l) * 100))
        pos["w52_pct"] = round(wp, 1)
        fmt["w52_low"] = f"{csym}{_fmt_num(w52l, decimals=pdec)}"
        fmt["w52_high"] = f"{csym}{_fmt_num(w52h, decimals=pdec)}"
        fmt["w52_pos_label"] = f"현재가 ({wp:.0f}%)"

    if target_mean:
        fmt["target_mean"] = f"{csym}{_fmt_num(target_mean, decimals=pdec)}"
        if price and price > 0:
            up = (target_mean - price) / price * 100
            pos["upside_pos"] = bool(up >= 0)
            fmt["upside"] = f"{'+' if up >= 0 else ''}{up:.1f}%"

    if target_low and target_high and price and target_high > target_low:
        rng = target_high - target_low
        tp = max(0.0, min(100.0, (price - target_low) / rng * 100))
        mp = max(0.0, min(100.0, ((target_mean or price) - target_low) / rng * 100))
        pos["tgt_pct"] = round(tp, 1)
        pos["tgt_mean_pct"] = round(mp, 1)
        pos["tgt_fill_pos"] = bool(mp > tp)
        fmt["tgt_low"] = f"{csym}{_fmt_num(target_low, decimals=pdec)}"
        fmt["tgt_high"] = f"{csym}{_fmt_num(target_high, decimals=pdec)}"
        fmt["tgt_mean"] = f"목표 {csym}{_fmt_num(target_mean, decimals=pdec)}"

    if not fmt:
        return None
    _kst = datetime.timezone(datetime.timedelta(hours=9))
    return {
        "mode": "light",
        "fmt": fmt,
        "pos": pos,
        "meta": {"source": source, "delayed": delayed,
                 "ts": datetime.datetime.now(_kst).strftime("%H:%M")},
    }


def _render_stock_info_html(rec: dict) -> str:
    """Render header cards + tabbed company info sections from stock_info."""
    si = rec.get("stock_info")
    if not si:
        return ""
    esc = _html.escape

    ticker = rec.get("ticker", "")
    currency = si.get("currency", "USD")
    csym = _currency_sym(currency)

    # ── top header cards ────────────────────────────────────────
    price = si.get("current_price")
    _0dec = ("KRW", "JPY", "TWD", "HKD")
    price_str = _fmt_num(price, decimals=2 if currency not in _0dec else 0,
                         prefix=csym)
    mcap_str = _fmt_mcap(si.get("market_cap"), csym, currency)
    shares_str = _fmt_shares(si.get("shares_outstanding"))
    ne = si.get("next_earnings", "")
    ne_label = ne if ne else "—"
    ne_sub = "(추정)" if ne else ""

    header = f"""<div class="si-header">
  <div class="si-card"><span class="si-label">현재가</span>
    <span class="si-value" data-q="price">{esc(price_str)}</span></div>
  <div class="si-card"><span class="si-label">시가총액</span>
    <span class="si-value" data-q="mcap">{esc(mcap_str)}</span></div>
  <div class="si-card"><span class="si-label">발행주식수</span>
    <span class="si-value">{esc(shares_str)}</span></div>
  <div class="si-card"><span class="si-label">다음 실적</span>
    <span class="si-value">{esc(ne_label)}</span>
    <span class="si-sub">{esc(ne_sub)}</span></div>
</div>
<div class="si-quote-status" style="margin:-8px 0 16px;font-size:11px;color:var(--fg-soft)">
  <span id="q-badge" style="font-weight:600"></span>
  <span class="si-quote-note">가격연동 지표(현재가·시총·PER·선행PER·PBR·PSR·EV/EBITDA·배당·베타·EPS·BPS·52주·이평·컨센서스)는 라이브 · 재무제표·실적·주주·수급·공시·리서치·뉴스는 최신 공시/일 단위 갱신 · 차트는 분석 시점 저장본</span>
</div>"""

    # ── market detection — pick the right market-specific sub-dict ──
    is_kr = ticker.endswith((".KS", ".KQ"))
    is_jp = ticker.endswith(".T")
    is_tw = ticker.endswith((".TW", ".TWO"))
    is_cn = ticker.endswith((".SS", ".SZ", ".BJ", ".HK"))
    is_us = not (is_kr or is_jp or is_tw or is_cn)
    # Live network enrichment (KR 외국인보유·DART, JP JPX, TW 三大法人, US
    # options/Form4/finnhub) must NEVER run on the bot's event loop during a
    # batch regen — dozens of cold-cache HTTP calls block Telegram polling
    # >180s and trip the watchdog (restart → startup regen → cold cache →
    # hang again, the vicious loop the user kept seeing). The on-demand
    # /api/quote?full=1 path re-fetches ALL of these live in the dashboard
    # *server* process and swaps them in, so the static page loses nothing.
    # Every live-fetch block below is gated on `_live`; keep it that way.
    _live = not _BATCH_REGEN
    if is_kr:
        mkt = si.get("kr", {})
    elif is_jp:
        mkt = si.get("jp", {})
    elif is_tw:
        mkt = si.get("tw", {})
    elif is_cn:
        mkt = si.get("cn", {})
    else:
        mkt = si.get("us", {})
    kr = si.get("kr", {})

    # ── tab navigation ──────────────────────────────────────────
    has_disclosures = bool(mkt.get("disclosures"))
    disclosure_tab = '  <button type="button" class="si-tab" data-pane="si-disclosures">공시</button>\n' if has_disclosures else ""
    has_flow = bool(is_kr and kr.get("flow")) or bool(is_cn and mkt.get("hsgt_flow")) or is_jp or is_tw or is_us
    flow_tab = '  <button type="button" class="si-tab" data-pane="si-flow">수급</button>\n' if has_flow else ""
    has_risk = bool(kr.get("lockup_releases") or kr.get("dilution_events") or kr.get("market_alert")) or bool(is_cn and mkt.get("risk_status"))
    risk_tab = '  <button type="button" class="si-tab" data-pane="si-risk">리스크</button>\n' if has_risk else ""
    has_financials = bool(si.get("financials"))
    financials_tab = '  <button type="button" class="si-tab" data-pane="si-financials">재무제표</button>\n' if has_financials else ""
    has_peers = bool(si.get("peer_comps"))
    peers_tab = '  <button type="button" class="si-tab" data-pane="si-peers">동종비교</button>\n' if has_peers else ""
    tabs_html = f"""<div class="si-tabs" id="si-tab-bar">
  <button type="button" class="si-tab active" data-pane="si-overview">종합</button>
  <button type="button" class="si-tab" data-pane="si-company">기업</button>
  <button type="button" class="si-tab" data-pane="si-consensus">컨센서스</button>
  <button type="button" class="si-tab" data-pane="si-valuation">밸류에이션</button>
{financials_tab}{peers_tab}  <button type="button" class="si-tab" data-pane="si-earnings">실적</button>
  <button type="button" class="si-tab" data-pane="si-research">리서치</button>
  <button type="button" class="si-tab" data-pane="si-holders">주주</button>
{flow_tab}{disclosure_tab}{risk_tab}  <button type="button" class="si-tab" data-pane="si-news">뉴스</button>
  <button type="button" class="si-tab" data-pane="si-timeline">타임라인</button>
</div>"""

    # ── 기업 정보 pane ──────────────────────────────────────────
    from bot.translate import (sector_kr, industry_kr, country_kr,
                               grade_kr, action_kr,
                               translate_description_kr, translate_news_titles_kr)
    desc = si.get("description", "")
    if desc:
        desc = translate_description_kr(desc)
    desc_html = f'<div class="si-desc">{esc(desc[:2000])}</div>' if desc else ""

    def grid_row(key, val, qkey=""):
        qattr = f' data-q="{qkey}"' if qkey else ""
        return f'<div class="si-row"><div class="si-key">{esc(key)}</div><div class="si-val"{qattr}>{esc(str(val) if val else "—")}</div></div>'

    exchange = si.get("exchange", "")
    # Map exchange codes to readable names
    ex_map = {"KSE": "KOSPI", "KOE": "KOSPI", "KSQ": "KOSDAQ", "NMS": "NASDAQ",
              "NYQ": "NYSE", "NGM": "NASDAQ", "JPX": "TSE", "TYO": "TSE",
              "TWO": "TPEx", "TAI": "TWSE", "HKG": "HKEX", "SHH": "SSE", "SHZ": "SZSE"}
    exchange_display = ex_map.get(exchange, exchange)

    website = si.get("website", "")
    website_html = f'<a href="{esc(website)}" target="_blank" rel="noopener">{esc(website)}</a>' if website else "—"

    employees = si.get("employees")
    emp_str = f"{employees:,}" if employees else "—"

    fy = si.get("fiscal_year_end", "")
    ftd = si.get("first_trade_date", "")

    # KR-specific fields from DART/FSC
    kr_basic_rows = ""
    kr_company_rows = ""
    kr_financial_html = ""
    if kr:
        if kr.get("corp_name"):
            kr_basic_rows += grid_row("법인명", kr["corp_name"])
        if kr.get("corp_reg_no"):
            kr_basic_rows += grid_row("법인등록번호", kr["corp_reg_no"])
        if kr.get("ksic_code"):
            kr_basic_rows += grid_row("산업분류 (KSIC)", kr["ksic_code"])
        if kr.get("fiscal_month"):
            kr_basic_rows += grid_row("결산월", f"{kr['fiscal_month']}월")
        if kr.get("ceo"):
            kr_company_rows += grid_row("대표자", kr["ceo"])
        if kr.get("address"):
            kr_company_rows += grid_row("주소", kr["address"])
        if kr.get("established"):
            est = kr["established"]
            if len(est) == 8:
                est = f"{est[:4]}-{est[4:6]}-{est[6:]}"
            kr_company_rows += grid_row("설립일", est)
        # minority holders
        mh = kr.get("minority", {})
        if mh.get("smam_ratio"):
            kr_company_rows += grid_row("소액주주 비율", f"{mh['smam_ratio']:.1f}%")
        # K-IFRS financials summary
        kf = kr.get("financials", {})
        if kf:
            def _krw_eok(v):
                if v is None:
                    return "—"
                return f"{v / 1e8:,.0f}억" if abs(v) >= 1e8 else f"{v:,.0f}"
            fy_label = f"FY{kf.get('year', '')}" if kf.get("year") else ""
            fs_label = "(연결)" if kf.get("fs_div") == "CFS" else "(별도)" if kf.get("fs_div") == "OFS" else ""
            fin_rows = ""
            for label, key in (("매출", "매출"), ("영업이익", "영업이익"),
                               ("당기순이익", "당기순이익"), ("자산총계", "자산총계"),
                               ("부채총계", "부채총계"), ("자본총계", "자본총계")):
                v = kf.get(key)
                fin_rows += f'<tr><td>{esc(label)}</td><td class="num">{_krw_eok(v)}</td></tr>\n'
            ratio_rows = ""
            for label, key in (("영업이익률", "영업이익률"), ("순이익률", "순이익률"),
                               ("ROE", "ROE"), ("ROA", "ROA"),
                               ("부채비율", "부채비율"), ("유동비율", "유동비율")):
                v = kf.get(key)
                ratio_rows += f'<tr><td>{esc(label)}</td><td class="num">{f"{v:.1f}%" if v is not None else "—"}</td></tr>\n'
            kr_financial_html = f"""<div class="si-section">
    <div class="si-section-title">K-IFRS 재무 요약 {esc(fy_label)} {esc(fs_label)}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
      <table class="si-table"><thead><tr><th>항목</th><th class="num">금액 (₩)</th></tr></thead><tbody>{fin_rows}</tbody></table>
      <table class="si-table"><thead><tr><th>비율</th><th class="num">값</th></tr></thead><tbody>{ratio_rows}</tbody></table>
    </div>
  </div>"""

    company_pane = f"""<div class="si-pane" id="si-company">
  <div class="si-section">
    <div class="si-section-title">개요</div>
    {desc_html}
  </div>
  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
    <div class="si-section">
      <div class="si-section-title">기본 정보</div>
      <div class="si-grid">
        {grid_row("티커", ticker)}
        {grid_row("거래소", exchange_display)}
        {grid_row("섹터", sector_kr(si.get("sector", "")))}
        {grid_row("산업", industry_kr(si.get("industry", "")))}
        {kr_basic_rows}{grid_row("회계연도 종료", fy)}
        {grid_row("통화", currency)}
        {grid_row("상장일", ftd)}
      </div>
    </div>
    <div class="si-section">
      <div class="si-section-title">회사 정보</div>
      <div class="si-grid">
        {grid_row("국가", country_kr(si.get("country", "")))}
        {grid_row("지역", si.get("city") or si.get("state") or "")}
        {grid_row("임직원", emp_str)}
        {kr_company_rows}<div class="si-row"><div class="si-key">홈페이지</div><div class="si-val">{website_html}</div></div>
      </div>
    </div>
  </div>
  <div class="si-section">
    <div class="si-section-title">시장 정보</div>
    <div class="si-grid">
      {grid_row("시가총액", mcap_str, "mcap")}
      {grid_row("현재가", price_str, "price")}
      {grid_row("발행주식수", shares_str)}
    </div>
  </div>
  {kr_financial_html}
</div>"""

    # ── 컨센서스 pane ───────────────────────────────────────────
    rec_key = (si.get("recommendation_key") or "").lower()
    rec_map = {"buy": "매수", "strong_buy": "강력 매수", "hold": "보유",
               "sell": "매도", "strong_sell": "강력 매도", "overweight": "비중확대",
               "underweight": "비중축소"}
    rec_label = rec_map.get(rec_key, rec_key or "—")
    rec_class = "buy" if rec_key in ("buy", "strong_buy", "overweight") else \
                "sell" if rec_key in ("sell", "strong_sell", "underweight") else "hold"

    target_mean = si.get("target_mean")
    target_high = si.get("target_high")
    target_low = si.get("target_low")
    n_analysts = si.get("num_analysts")
    cur_price = si.get("current_price")

    upside_str = '<span class="pct" data-q="upside"></span>'
    if target_mean and cur_price and cur_price > 0:
        upside = (target_mean - cur_price) / cur_price * 100
        sign = "+" if upside >= 0 else ""
        upside_str = f'<span class="pct" data-q="upside" style="color:{"#26a69a" if upside >= 0 else "#e2574c"}">{sign}{upside:.1f}%</span>'

    # Range bar
    range_html = ""
    if target_low and target_high and cur_price and target_high > target_low:
        rng = target_high - target_low
        if rng > 0:
            cur_pct = max(0, min(100, (cur_price - target_low) / rng * 100))
            mean_pct = max(0, min(100, ((target_mean or cur_price) - target_low) / rng * 100))
            range_html = f"""<div class="si-range-bar">
  <div class="si-range-track">
    <div class="si-range-fill" data-qfill="tgt" style="left:0;width:{mean_pct:.1f}%;background:{"#26a69a" if mean_pct > cur_pct else "#e2574c"};opacity:0.3;"></div>
    <div class="si-range-marker" data-qmark="tgt" style="left:{cur_pct:.1f}%" title="현재가"></div>
  </div>
  <div class="si-range-labels">
    <span data-q="tgt_low">{csym}{_fmt_num(target_low, decimals=2 if currency not in _0dec else 0)}</span>
    <span data-q="tgt_mean">목표 {csym}{_fmt_num(target_mean, decimals=2 if currency not in _0dec else 0)}</span>
    <span data-q="tgt_high">{csym}{_fmt_num(target_high, decimals=2 if currency not in _0dec else 0)}</span>
  </div>
</div>"""

    analysts_str = f" · {n_analysts}명 애널리스트" if n_analysts else ""

    # Supplementary consensus from market-specific sources (when yfinance empty)
    supp_consensus_html = ""
    supp_con = mkt.get("consensus", {})
    if supp_con and supp_con.get("target_mean"):
        sc_src = esc(supp_con.get("source", ""))
        sc_target = supp_con["target_mean"]
        sc_rating = esc(str(supp_con.get("rating", "—")))
        sc_n = supp_con.get("n_analysts")
        sc_n_str = f" · {sc_n}명" if sc_n else ""
        sc_lrd = supp_con.get("last_report_date")
        sc_lrd_str = f" · 최신 {esc(sc_lrd)}" if sc_lrd else ""
        sc_upside = ""
        if cur_price and cur_price > 0:
            sc_up = (sc_target - cur_price) / cur_price * 100
            sc_sign = "+" if sc_up >= 0 else ""
            sc_color = "#26a69a" if sc_up >= 0 else "#e2574c"
            sc_upside = f' <span style="color:{sc_color}">{sc_sign}{sc_up:.1f}%</span>'
        supp_consensus_html = f"""<div class="si-section" style="margin-top:16px">
    <div class="si-section-title">{sc_src} 컨센서스</div>
    <div style="font-size:14px">
      목표가 {csym}{_fmt_num(sc_target, decimals=2 if currency not in _0dec else 0)}{sc_upside} · {sc_rating}{sc_n_str}{sc_lrd_str}
    </div>
  </div>"""

    consensus_pane = f"""<div class="si-pane" id="si-consensus">
  <div class="si-section">
    <div class="si-section-title">{"월가 컨센서스" if is_us else "애널리스트 컨센서스"}</div>
    <div class="si-consensus">
      <div>
        <span class="si-con-badge {rec_class}">{esc(rec_label)}</span>
      </div>
      <div class="si-con-target">
        목표가 <span data-q="target_mean">{csym}{_fmt_num(target_mean, decimals=2 if currency not in _0dec else 0)}</span> {upside_str}{analysts_str}
      </div>
    </div>
    {range_html}
  </div>
  {supp_consensus_html}
</div>"""

    # ── 밸류에이션 pane ────────────────────────────────────────
    def _val_row(label, val, suffix="", qkey=""):
        qattr = f' data-q="{qkey}"' if qkey else ""
        if val is None:
            return f'<tr><td>{esc(label)}</td><td class="num"{qattr}>—</td></tr>\n'
        if isinstance(val, float):
            return f'<tr><td>{esc(label)}</td><td class="num"{qattr}>{val:,.2f}{suffix}</td></tr>\n'
        return f'<tr><td>{esc(label)}</td><td class="num"{qattr}>{val}{suffix}</td></tr>\n'

    # 52-week position bar
    w52_high = si.get("fiftyTwoWeekHigh")
    w52_low = si.get("fiftyTwoWeekLow")
    w52_bar_html = ""
    if w52_high and w52_low and cur_price and w52_high > w52_low:
        w52_rng = w52_high - w52_low
        w52_pct = max(0, min(100, (cur_price - w52_low) / w52_rng * 100))
        w52_bar_html = f"""<div class="si-section">
    <div class="si-section-title">52주 레인지 내 위치</div>
    <div class="si-range-bar">
      <div class="si-range-track">
        <div class="si-range-fill" data-qfill="w52" style="left:0;width:{w52_pct:.1f}%;background:#42a5f5;opacity:0.3;"></div>
        <div class="si-range-marker" data-qmark="w52" style="left:{w52_pct:.1f}%" title="현재가"></div>
      </div>
      <div class="si-range-labels">
        <span data-q="w52_low">{csym}{_fmt_num(w52_low, decimals=2 if currency not in _0dec else 0)}</span>
        <span data-q="w52_pos_label">현재가 ({w52_pct:.0f}%)</span>
        <span data-q="w52_high">{csym}{_fmt_num(w52_high, decimals=2 if currency not in _0dec else 0)}</span>
      </div>
    </div>
  </div>"""

    val_multiples = ""
    val_multiples += _val_row("PER (후행)", si.get("trailingPE"), "x", "trailingPE")
    val_multiples += _val_row("PER (선행)", si.get("forwardPE"), "x", "forwardPE")
    val_multiples += _val_row("PBR (주가순자산)", si.get("priceToBook"), "x", "priceToBook")
    val_multiples += _val_row("PSR (주가매출)", si.get("priceToSalesTrailing12Months"), "x", "priceToSalesTrailing12Months")
    val_multiples += _val_row("EV/EBITDA", si.get("enterpriseToEbitda"), "x", "enterpriseToEbitda")
    _dy_pct = _safe_dy_pct(si.get("dividendYield"), si.get("dividendRate"), si.get("current_price"))
    val_multiples += _val_row("배당수익률", _dy_pct, "%", "dividendYield")
    val_multiples += _val_row("베타", si.get("beta"), "", "beta")

    val_per_share = ""
    val_per_share += _val_row("EPS (후행)", si.get("trailingEps"), "", "trailingEps")
    val_per_share += _val_row("EPS (선행)", si.get("forwardEps"), "", "forwardEps")
    val_per_share += _val_row("BPS (주당순자산)", si.get("bookValue"), "", "bookValue")
    val_per_share += _val_row("50일 이동평균", si.get("fiftyDayAverage"), "", "fiftyDayAverage")
    val_per_share += _val_row("200일 이동평균", si.get("twoHundredDayAverage"), "", "twoHundredDayAverage")

    # KR financials multi-year (if available)
    kr_fin_ts_html = ""
    kr_fin_ts = kr.get("financials_ts")
    if kr_fin_ts and len(kr_fin_ts) > 1:
        ts_header = "<th>항목</th>" + "".join(f"<th class='num'>FY{esc(str(yr.get('year','')))}</th>" for yr in kr_fin_ts)
        ts_rows = ""
        for label, key in (("매출", "매출"), ("영업이익", "영업이익"), ("당기순이익", "당기순이익")):
            cells = ""
            for yr in kr_fin_ts:
                v = yr.get(key)
                cells += f"<td class='num'>{v / 1e8:,.0f}억</td>" if v else "<td class='num'>—</td>"
            ts_rows += f"<tr><td>{esc(label)}</td>{cells}</tr>\n"
        # Ratios
        for label, key in (("영업이익률", "영업이익률"), ("ROE", "ROE"), ("부채비율", "부채비율")):
            cells = ""
            for yr in kr_fin_ts:
                v = yr.get(key)
                cells += f"<td class='num'>{v:.1f}%</td>" if v is not None else "<td class='num'>—</td>"
            ts_rows += f"<tr><td>{esc(label)}</td>{cells}</tr>\n"
        kr_fin_ts_html = f"""<div class="si-section">
    <div class="si-section-title">재무 추이 (K-IFRS 연결)</div>
    <table class="si-table"><thead><tr>{ts_header}</tr></thead><tbody>{ts_rows}</tbody></table>
  </div>"""

    # valuation_pane assembled later (after div_html / us_xbrl_html defined)

    # ── 실적 pane ───────────────────────────────────────────────
    earnings = si.get("earnings_history", [])
    if earnings:
        e_rows = ""
        for e in earnings:
            d = esc(e.get("date", "—"))
            eps_est = e.get("EPS Estimate")
            eps_act = e.get("Reported EPS")
            surprise = e.get("Surprise(%)")
            _ed = 0 if currency in _0dec else 2
            eps_est_str = f"{eps_est:,.{_ed}f}" if eps_est is not None else "—"
            eps_act_str = f"{eps_act:,.{_ed}f}" if eps_act is not None else "—"
            if surprise is not None:
                s_cls = "pos" if surprise >= 0 else "neg"
                s_str = f'<span class="{s_cls}">{surprise:+.1f}%</span>'
            else:
                s_str = "—"
            e_rows += f"<tr><td>{d}</td><td class='num'>{eps_est_str}</td><td class='num'>{eps_act_str}</td><td class='num'>{s_str}</td></tr>\n"
        earnings_table = f"""<table class="si-table">
  <thead><tr><th>발표일</th><th class="num">EPS 예상</th><th class="num">EPS 실제</th><th class="num">서프라이즈</th></tr></thead>
  <tbody>{e_rows}</tbody></table>"""
    else:
        earnings_table = '<div class="si-empty">실적 데이터가 없습니다.</div>'

    # ── TW 월매출 (營收) ────────────────────────────────────────
    tw_revenue_html = ""
    tw_rev = si.get("tw", {}).get("monthly_revenue") if isinstance(si.get("tw"), dict) else None
    if tw_rev and isinstance(tw_rev, list):
        tw_r_rows = ""
        sorted_rev = sorted(tw_rev, key=lambda r: r.get("date", ""), reverse=True)
        # build date→revenue map for YoY/MoM fallback computation
        _rev_map: dict[str, float] = {}
        for _r in sorted_rev:
            _d = _r.get("date", "")
            _rv = _r.get("revenue")
            if _d and _rv is not None:
                _rev_map[_d] = float(_rv)
        for r in sorted_rev[:13]:
            d = esc(r.get("date", "—"))
            rev_val = r.get("revenue")
            yoy = r.get("revenue_year_growth_rate")
            mom = r.get("revenue_month_growth_rate")
            # compute YoY/MoM from raw revenue when API returns null
            raw_d = r.get("date", "")
            if rev_val is not None and raw_d and len(raw_d) >= 7:
                try:
                    _y, _m = int(raw_d[:4]), int(raw_d[5:7])
                    if yoy is None:
                        prev_y_key = f"{_y - 1}-{_m:02d}-01"
                        prev_y_rev = _rev_map.get(prev_y_key)
                        if prev_y_rev and prev_y_rev > 0:
                            yoy = (float(rev_val) / prev_y_rev - 1) * 100
                    if mom is None:
                        pm_y, pm_m = (_y, _m - 1) if _m > 1 else (_y - 1, 12)
                        prev_m_key = f"{pm_y}-{pm_m:02d}-01"
                        prev_m_rev = _rev_map.get(prev_m_key)
                        if prev_m_rev and prev_m_rev > 0:
                            mom = (float(rev_val) / prev_m_rev - 1) * 100
                except (ValueError, ZeroDivisionError):
                    pass
            rev_str = f"{rev_val:,.0f}" if rev_val is not None else "—"
            if yoy is not None:
                y_cls = "pos" if yoy >= 0 else "neg"
                yoy_str = f'<span class="{y_cls}">{yoy:+.1f}%</span>'
            else:
                yoy_str = "—"
            if mom is not None:
                m_cls = "pos" if mom >= 0 else "neg"
                mom_str = f'<span class="{m_cls}">{mom:+.1f}%</span>'
            else:
                mom_str = "—"
            tw_r_rows += f"<tr><td>{d}</td><td class='num'>{rev_str}</td><td class='num'>{yoy_str}</td><td class='num'>{mom_str}</td></tr>\n"
        tw_revenue_html = f"""<div class="si-section">
    <div class="si-section-title">TW 월매출 (營收)</div>
    <table class="si-table">
  <thead><tr><th>날짜</th><th class="num">매출(千元)</th><th class="num">YoY</th><th class="num">MoM</th></tr></thead>
  <tbody>{tw_r_rows}</tbody></table>
    <div style="margin-top:8px;font-size:12px;color:var(--fg-soft)">출처: FinMind/TWSE · 상장사 매월 10일 이내 의무 공시</div>
  </div>"""

    earnings_pane = f"""<div class="si-pane" id="si-earnings">
  <div class="si-section">
    <div class="si-section-title">최근 실적</div>
    {earnings_table}
  </div>
  {tw_revenue_html}
</div>"""

    # ── 리서치 pane ─────────────────────────────────────────────
    # US: yfinance upgrades/downgrades (firm + from→to grade events).
    # KR: 한경 컨센서스 per-broker reports (발행일/증권사/투자의견/목표가)
    # — yfinance has no KR upgrade/downgrade feed, so this is the
    # equivalent surface for Korean equities.
    upgrades = si.get("upgrades_downgrades", [])
    kr_reports = kr.get("research_reports", [])
    if upgrades:
        r_rows = ""
        for u in upgrades:
            d = esc(u.get("date", "—"))
            firm = esc(u.get("Firm", "—"))
            to_g = esc(grade_kr(u.get("ToGrade", "—")))
            from_g = esc(grade_kr(u.get("FromGrade", "")))
            action = esc(action_kr(u.get("Action", "")))
            change = f"{from_g} → {to_g}" if from_g else to_g
            r_rows += f"<tr><td>{d}</td><td>{firm}</td><td>{action}</td><td>{change}</td></tr>\n"
        research_table = f"""<table class="si-table">
  <thead><tr><th>날짜</th><th>리서치펌</th><th>액션</th><th>의견 변화</th></tr></thead>
  <tbody>{r_rows}</tbody></table>"""
    elif kr_reports:
        r_rows = ""
        for rp in kr_reports:
            d = esc(str(rp.get("date", "—")))
            broker = esc(str(rp.get("broker", "—")))
            rating = esc(grade_kr(str(rp.get("rating", "")))) or "—"
            tgt = rp.get("target")
            tgt_str = f"₩{int(tgt):,}" if tgt else "—"
            r_rows += f"<tr><td>{d}</td><td>{broker}</td><td>{rating}</td><td class='num'>{tgt_str}</td></tr>\n"
        research_table = f"""<table class="si-table">
  <thead><tr><th>발행일</th><th>증권사</th><th>투자의견</th><th class="num">목표가</th></tr></thead>
  <tbody>{r_rows}</tbody></table>
  <div style="margin-top:8px;font-size:12px;color:var(--fg-soft)">출처: Naver Finance · 최근 90일 증권사 리포트</div>"""
    else:
        research_table = '<div class="si-empty">리서치 액션 데이터가 없습니다.</div>'

    research_pane = f"""<div class="si-pane" id="si-research">
  <div class="si-section">
    <div class="si-section-title">리서치 액션</div>
    {research_table}
  </div>
</div>"""

    # ── 기관 pane ───────────────────────────────────────────────
    holders = si.get("institutional_holders", [])
    ins_pct = si.get("held_pct_insiders")
    inst_pct = si.get("held_pct_institutions")
    holder_summary = ""
    if ins_pct is not None or inst_pct is not None:
        parts = []
        if inst_pct is not None:
            parts.append(f"기관 보유: {inst_pct * 100:.1f}%")
        if ins_pct is not None:
            parts.append(f"내부자 보유: {ins_pct * 100:.1f}%")
        holder_summary = f'<div style="margin-bottom:10px;font-size:13px;color:var(--fg-soft)">{" · ".join(parts)}</div>'

    shares_out = si.get("shares_outstanding")
    if holders:
        h_rows = ""
        for h in holders:
            name = esc(str(h.get("Holder", "—")))
            shares = h.get("Shares")
            pct = h.get("% Out")
            if not pct and shares and shares_out and shares_out > 0:
                pct = shares / shares_out
            val = h.get("Value")
            shares_str = f"{int(shares):,}" if shares else "—"
            pct_str = f"{pct * 100:.2f}%" if pct else "—"
            val_str = f"${val:,.0f}" if val else "—"
            date_r = esc(str(h.get("Date Reported", "—")))
            h_rows += f"<tr><td>{name}</td><td class='num'>{shares_str}</td><td class='num'>{pct_str}</td><td class='num'>{val_str}</td><td>{date_r}</td></tr>\n"
        holders_table = f"""<table class="si-table">
  <thead><tr><th>기관명</th><th class="num">보유주식</th><th class="num">비중</th><th class="num">평가액</th><th>보고일</th></tr></thead>
  <tbody>{h_rows}</tbody></table>"""
    else:
        holders_table = '<div class="si-empty">기관 보유 데이터가 없습니다.</div>'

    # KR DART insider holdings (임원·주요주주)
    kr_insider_html = ""
    kr_insiders = kr.get("insider_holdings", [])
    if kr_insiders:
        ki_rows = ""
        for ih in kr_insiders:
            nm = esc(str(ih.get("name", "—")))
            role = esc(str(ih.get("role", "")))
            shares_i = ih.get("shares")
            shares_i_str = f"{int(shares_i):,}" if shares_i else "—"
            pct_i = ih.get("pct")
            pct_i_str = f"{pct_i:.2f}%" if pct_i is not None else "—"
            ch_date = esc(str(ih.get("changed_on", "—")))
            ki_rows += f"<tr><td>{nm}</td><td>{role}</td><td class='num'>{shares_i_str}</td><td class='num'>{pct_i_str}</td><td>{ch_date}</td></tr>\n"
        kr_insider_html = f"""<div class="si-section">
    <div class="si-section-title">임원 · 주요주주 지분 (DART)</div>
    <table class="si-table">
      <thead><tr><th>성명</th><th>직위</th><th class="num">보유주식</th><th class="num">지분율</th><th>변동일</th></tr></thead>
      <tbody>{ki_rows}</tbody>
    </table>
  </div>"""

    # KR minority holders summary
    kr_minority_html = ""
    kr_minority = kr.get("minority", {})
    if kr_minority.get("smam_ratio") is not None:
        smam = kr_minority
        kr_minority_html = f"""<div style="margin:10px 0;font-size:13px;color:var(--fg-soft)">
  소액주주: {smam.get('smam_cnt', '—'):,}명 / 전체 {smam.get('whole_cnt', '—'):,}명 · 비율 {smam.get('smam_ratio', 0):.1f}% · 기준 {esc(str(smam.get('biz_year', '')))}
</div>"""

    # ── P5 KR: 외국인보유 상세 (pykrx → Seibro → Naver fallback) ──
    kr_foreign_html = ""
    if is_kr and _live:
        try:
            fh = None
            try:
                from bot.pykrx_client import get_kr_foreign_holding
                fh = get_kr_foreign_holding(ticker)
            except Exception:
                pass
            if fh is None:
                from bot.seibro_client import fetch_foreign_holding, seibro_key_ready
                if seibro_key_ready():
                    fh = fetch_foreign_holding(ticker)
            if fh is None:
                try:
                    from bot.naver_finance_client import get_naver_foreign_holding
                    fh = get_naver_foreign_holding(ticker)
                except Exception:
                    pass
            if fh:
                fp = fh.get("foreign_pct", 0)
                fs = fh.get("foreign_shares", 0)
                ls = fh.get("listed_shares", 0)
                le = fh.get("limit_exhaustion_pct")
                lim_s = fh.get("limit_shares", 0)
                fd = esc(str(fh.get("date", "")))
                _src_map = {"krx": "KRX", "naver": "네이버"}
                src = _src_map.get(fh.get("source", ""), "세이브로/KSD")
                le_html = ""
                if le is not None:
                    le_warn = " ⚠️ ceiling 임박" if le >= 95 else (" ⚠️ 제한 접근" if le >= 80 else "")
                    le_html = f'<tr><td>한도소진율</td><td class="num">{le:.2f}%{le_warn}</td></tr><tr><td>한도주식수</td><td class="num">{lim_s:,}주</td></tr>'
                kr_foreign_html = f"""<div class="si-section">
    <div class="si-section-title">외국인 보유현황 ({src} · {fd})</div>
    <table class="si-table"><tbody>
      <tr><td>보유비율</td><td class="num">{fp:.2f}%</td></tr>
      <tr><td>보유주식수</td><td class="num">{fs:,}주</td></tr>
      <tr><td>상장주식수</td><td class="num">{ls:,}주</td></tr>
      {le_html}
    </tbody></table></div>"""
            else:
                log.info("foreign holding: pykrx+seibro+naver all None for %s", ticker)
        except Exception as exc:
            log.warning("detail: seibro foreign %s: %s", ticker, exc)

    # ── P6 KR: DART 최대주주 현황 (주주탭 하단) ──────────────────
    kr_affiliates_html = ""
    if is_kr and _live:
        try:
            from bot.dart_client import get_dart
            dart = get_dart()
            if dart:
                stock_code = ticker.split(".")[0]
                shareholders = dart.get_major_shareholders(stock_code)
                if shareholders:
                    sh_rows = ""
                    for sh in shareholders[:20]:
                        sh_nm = esc(sh.get("name", ""))
                        sh_rel = esc(sh.get("relation", ""))
                        sh_shares = sh.get("shares", 0)
                        sh_pct = sh.get("pct", 0.0)
                        sh_shares_str = f"{sh_shares:,}" if sh_shares not in (None, 0) else "—"
                        sh_pct_str = f"{sh_pct:.2f}%" if sh_pct not in (None, 0, 0.0) else "—"
                        sh_note = esc(sh.get("note", ""))
                        sh_rows += f"<tr><td>{sh_nm}</td><td>{sh_rel}</td><td class='num'>{sh_shares_str}</td><td class='num'>{sh_pct_str}</td></tr>\n"
                    kr_affiliates_html = f"""<div class="si-section">
    <div class="si-section-title">최대주주 현황 (DART 사업보고서 · {len(shareholders)}명)</div>
    <table class="si-table"><thead><tr><th>성명(회사명)</th><th>관계</th><th>소유주식수</th><th>지분율</th></tr></thead>
    <tbody>{sh_rows}</tbody></table></div>"""
                else:
                    log.info("dart: get_major_shareholders(%s) returned empty", stock_code)
            else:
                log.info("dart: get_dart() returned None — DART_API_KEY not set?")
        except Exception as exc:
            log.warning("detail: DART major shareholders %s: %s", ticker, exc)

    # ── P6b KR: DART 계열회사(타법인 출자) 현황 (주주탭 하단) ──────
    kr_affiliates_invest_html = ""
    if is_kr and _live:
        try:
            from bot.dart_client import get_dart
            dart2 = get_dart()
            if dart2:
                stock_code2 = ticker.split(".")[0]
                investments = dart2.get_affiliate_investments(stock_code2)
                if investments:
                    ai_rows = ""
                    for inv in investments[:30]:
                        ai_nm = esc(inv.get("name", ""))
                        ai_purp = esc(inv.get("purpose", ""))
                        ai_pct = inv.get("pct", 0.0)
                        ai_bv = inv.get("book_value", 0)
                        ai_pct_str = f"{ai_pct:.2f}%" if ai_pct else "—"
                        if ai_bv and ai_bv > 0:
                            if ai_bv >= 1_0000_0000:
                                ai_bv_str = f"{ai_bv / 1_0000_0000:,.0f}억"
                            elif ai_bv >= 1_0000:
                                ai_bv_str = f"{ai_bv / 1_0000:,.0f}만"
                            else:
                                ai_bv_str = f"{ai_bv:,}"
                        else:
                            ai_bv_str = "—"
                        ai_rows += f"<tr><td>{ai_nm}</td><td>{ai_purp}</td><td class='num'>{ai_pct_str}</td><td class='num'>{ai_bv_str}</td></tr>\n"
                    kr_affiliates_invest_html = f"""<div class="si-section">
    <div class="si-section-title">계열회사(타법인 출자) 현황 (DART 사업보고서 · {len(investments)}사)</div>
    <table class="si-table"><thead><tr><th>법인명</th><th>투자목적</th><th>지분율</th><th>장부가액</th></tr></thead>
    <tbody>{ai_rows}</tbody></table></div>"""
                else:
                    log.info("dart: get_affiliate_investments(%s) returned empty", stock_code2)
        except Exception as exc:
            log.warning("detail: DART affiliate investments %s: %s", ticker, exc)

    # holders_pane assembled later (after us_insider_html defined)

    # ── 뉴스 pane ───────────────────────────────────────────────
    news = si.get("news", [])
    if news:
        # KR (Naver) news is already Korean — only send the rest (US
        # English / JP·TW·CN foreign titles) to the translator. kr_native
        # items pass through unchanged (₩0, no Gemini call).
        raw_titles = [n.get("title", "") for n in news
                      if n.get("title") and not n.get("kr_native")]
        title_kr_map = translate_news_titles_kr(raw_titles) if raw_titles else {}
        n_items = ""
        for n in news:
            raw_title = n.get("title", "")
            title = esc(title_kr_map.get(raw_title, raw_title))
            link = n.get("link", "")
            publisher = esc(n.get("publisher", ""))
            ndate = esc(n.get("date", ""))
            link_html = f'<a href="{esc(link)}" target="_blank" rel="noopener">{title}</a>' if link else title
            n_items += f'<div class="si-news-item"><div class="si-news-title">{link_html}</div><div class="si-news-meta">{publisher} · {ndate}</div></div>\n'
        news_html = n_items
    else:
        news_html = '<div class="si-empty">뉴스 데이터가 없습니다.</div>'

    news_pane = f"""<div class="si-pane" id="si-news">
  <div class="si-section">
    <div class="si-section-title">주요 뉴스</div>
    {news_html}
  </div>
</div>"""

    # ── 수급 pane (KR only — KIS + pykrx flow data) ─────────────
    flow_pane = ""
    kr_flow = kr.get("flow", {})
    if kr_flow:
        inv = kr_flow.get("investor_flow", {})
        # Investor flow table (today + 5d)
        inv_table = ""
        if inv:
            today = inv.get("today", {})
            five_d = inv.get("5d", {})
            def _flow_cell(v):
                if v is None:
                    return '<td class="num">—</td>'
                val = int(v)
                color = "#26a69a" if val > 0 else "#e2574c" if val < 0 else ""
                sign = "+" if val > 0 else ""
                style = f' style="color:{color}"' if color else ""
                return f'<td class="num"{style}>{sign}{val:,}만</td>'
            inv_table = f"""<div class="si-section">
      <div class="si-section-title">투자자별 순매수 (KIS)</div>
      <table class="si-table"><thead><tr><th>구분</th><th class="num">당일</th><th class="num">5일 누적</th></tr></thead><tbody>
        <tr><td>외국인</td>{_flow_cell(today.get("foreign"))}{_flow_cell(five_d.get("foreign"))}</tr>
        <tr><td>기관</td>{_flow_cell(today.get("institution"))}{_flow_cell(five_d.get("institution"))}</tr>
        <tr><td>개인</td>{_flow_cell(today.get("individual"))}{_flow_cell(five_d.get("individual"))}</tr>
      </tbody></table>
    </div>"""
            # Institution breakdown
            brk = inv.get("inst_breakdown", {})
            if brk:
                brk_rows = ""
                for label, key in (("연기금", "pension"), ("투신", "trust"), ("은행", "bank"),
                                   ("보험", "insurance"), ("사모", "private_eq"), ("기타법인", "etc_inst")):
                    v = brk.get(key)
                    brk_rows += f"<tr><td>{esc(label)}</td>{_flow_cell(v)}</tr>\n"
                inv_table += f"""<div class="si-section">
      <div class="si-section-title">기관 세부 (5일 누적)</div>
      <table class="si-table"><thead><tr><th>기관</th><th class="num">순매수 (5일)</th></tr></thead><tbody>{brk_rows}</tbody></table>
    </div>"""

        # Detailed multi-period investor flow (pykrx detail)
        # Skip live pykrx fetch during batch regen (startup/midnight) to avoid blocking polling
        inv_detail_html = ""
        if _live:
            try:
                from bot.pykrx_client import get_kr_investor_detail
                inv_d = get_kr_investor_detail(ticker)
                if inv_d and inv_d.get("investors"):
                    invs = inv_d["investors"]
                    unit = inv_d.get("unit", "억원")
                    p_hdrs = "".join(f'<th class="num">{p}</th>' for p in ("1d", "5d", "10d", "20d", "30d", "60d"))
                    d_rows = ""
                    for label, pds in invs.items():
                        cells = ""
                        for p in ("1d", "5d", "10d", "20d", "30d", "60d"):
                            v = pds.get(p)
                            if v is None or v == 0:
                                cells += '<td class="num">—</td>'
                            else:
                                c = "#26a69a" if v > 0 else "#e2574c"
                                sign = "+" if v > 0 else ""
                                cells += f'<td class="num" style="color:{c}">{sign}{v:,.1f}</td>'
                        d_rows += f"<tr><td>{esc(label)}</td>{cells}</tr>\n"
                    inv_detail_html = f"""<div class="si-section">
      <div class="si-section-title">투자주체별 순매수 다기간 ({unit})</div>
      <table class="si-table"><thead><tr><th>주체</th>{p_hdrs}</tr></thead><tbody>{d_rows}</tbody></table>
    </div>"""
            except Exception as exc:
                log.info("detail: investor detail %s: %s", ticker, exc)

        # Credit / Short / Program in a grid
        credit = kr_flow.get("credit", {})
        short_sale = kr_flow.get("short_sale", {})
        program = kr_flow.get("program", {})
        side_tables = ""
        if credit or short_sale:
            cs_rows = ""
            if credit.get("credit_balance_pct") is not None:
                cs_rows += f'<tr><td>신용잔고율</td><td class="num">{credit["credit_balance_pct"]:.2f}%</td></tr>\n'
            if credit.get("credit_balance_shares"):
                cs_rows += f'<tr><td>신용잔고 (주)</td><td class="num">{int(credit["credit_balance_shares"]):,}</td></tr>\n'
            if credit.get("loan_balance_shares"):
                cs_rows += f'<tr><td>대차잔고 (주)</td><td class="num">{int(credit["loan_balance_shares"]):,}</td></tr>\n'
            if short_sale.get("short_ratio_pct") is not None:
                cs_rows += f'<tr><td>공매도 비율</td><td class="num">{short_sale["short_ratio_pct"]:.2f}%</td></tr>\n'
            if short_sale.get("short_qty"):
                cs_rows += f'<tr><td>공매도 수량</td><td class="num">{int(short_sale["short_qty"]):,}</td></tr>\n'
            if cs_rows:
                side_tables += f"""<div class="si-section">
      <div class="si-section-title">신용·공매도</div>
      <table class="si-table"><thead><tr><th>항목</th><th class="num">값</th></tr></thead><tbody>{cs_rows}</tbody></table>
    </div>"""

        if program:
            pgm_rows = ""
            for label, key in (("비차익 순매수", "nonarb_net"), ("차익 순매수", "arb_net")):
                v = program.get(key)
                if v is not None:
                    val = int(v)
                    color = "#26a69a" if val > 0 else "#e2574c" if val < 0 else ""
                    sign = "+" if val > 0 else ""
                    style = f' style="color:{color}"' if color else ""
                    pgm_rows += f'<tr><td>{esc(label)}</td><td class="num"{style}>{sign}{val:,}만</td></tr>\n'
            if pgm_rows:
                side_tables += f"""<div class="si-section">
      <div class="si-section-title">프로그램 매매</div>
      <table class="si-table"><thead><tr><th>구분</th><th class="num">순매수</th></tr></thead><tbody>{pgm_rows}</tbody></table>
    </div>"""

        # Multi-period trends (live fetch from pykrx)
        # Skip during batch regen to avoid blocking polling
        trend_html = ""
        if _live:
            try:
                from bot.pykrx_client import get_kr_multi_period_trends
                mp = get_kr_multi_period_trends(ticker)
                if mp:
                    def _pp_cell(v, invert=False):
                        if v is None:
                            return '<td class="num">—</td>'
                        c = "#26a69a" if (v < 0 if invert else v > 0) else "#e2574c" if (v > 0 if invert else v < 0) else ""
                        s = f' style="color:{c}"' if c else ""
                        return f'<td class="num"{s}>{v:+.2f}</td>'
                    period_hdrs = "".join(f'<th class="num">{p}일</th>' for p in (5, 10, 20, 30, 60))
                    t_rows = ""
                    fo = mp.get("foreign", {})
                    if fo.get("current_pct") is not None:
                        pds = fo.get("periods", {})
                        cells = "".join(_pp_cell(pds.get(p)) for p in (5, 10, 20, 30, 60))
                        t_rows += f'<tr><td>외국인 보유율</td><td class="num">{fo["current_pct"]:.2f}%</td>{cells}</tr>\n'
                    sh = mp.get("short", {})
                    if sh.get("current_pct") is not None:
                        pds = sh.get("periods", {})
                        cells = "".join(_pp_cell(pds.get(p), invert=True) for p in (5, 10, 20, 30, 60))
                        t_rows += f'<tr><td>공매도 잔고율</td><td class="num">{sh["current_pct"]:.2f}%</td>{cells}</tr>\n'
                    if t_rows:
                        trend_html = f"""<div class="si-section">
      <div class="si-section-title">다기간 추이 (pp 변화)</div>
      <table class="si-table"><thead><tr><th>항목</th><th class="num">현재</th>{period_hdrs}</tr></thead><tbody>{t_rows}</tbody></table>
      <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">※ pp = 퍼센트포인트 = 현재 비율 − N거래일 전 비율(절대 차이). 예: 외국인 39.9%→40.4% = +0.5pp. 데이터가 그 기간만큼 없으면 부정확한 근사 대신 — 표시. 기관·연기금은 일별 보유비율 공시가 없어(외국인 보유율·공매도 잔고율만 일별 비율 존재) 이 표에 못 넣음 — 대신 위 「투자주체별 순매수 다기간」 표에 기관·연기금 순매수가 기간별로 있음.</div>
    </div>"""
            except Exception as exc:
                log.info("detail: multi-period trends %s: %s", ticker, exc)

        flow_pane = f"""<div class="si-pane" id="si-flow">
  {inv_table}
  {inv_detail_html}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
    {side_tables}
  </div>
  {trend_html}
</div>"""

    # CN/HK 港股通 flow
    if is_cn and not flow_pane:
        hsgt = mkt.get("hsgt_flow", {})
        if hsgt:
            def _cn_flow(v, unit="억"):
                if v is None:
                    return '<td class="num">—</td>'
                color = "#26a69a" if v > 0 else "#e2574c" if v < 0 else ""
                sign = "+" if v > 0 else ""
                style = f' style="color:{color}"' if color else ""
                return f'<td class="num"{style}>{sign}{v:,.1f}{unit}</td>'
            nb = hsgt.get("northbound_5d")
            sb = hsgt.get("southbound_5d")
            nd = hsgt.get("north_direction", "")
            sd = hsgt.get("south_direction", "")
            flow_pane = f"""<div class="si-pane" id="si-flow">
  <div class="si-section">
    <div class="si-section-title">港股通 자금 흐름 (5일 누적)</div>
    <table class="si-table"><thead><tr><th>구분</th><th class="num">5일 순매수</th><th>방향</th></tr></thead><tbody>
      <tr><td>北向 (외국인→A주)</td>{_cn_flow(nb)}<td>{esc(nd)}</td></tr>
      <tr><td>南向 (본토→HK)</td>{_cn_flow(sb)}<td>{esc(sd)}</td></tr>
    </tbody></table>
  </div>
</div>"""

    # ── JP: JPX 주간 투자주체별 수급 (시장 전체) ────────────────
    if is_jp and _live and not flow_pane:
        try:
            from bot.jpx_flow_client import fetch_jpx_weekly_flow
            jpx_rows = fetch_jpx_weekly_flow()
            if jpx_rows and len(jpx_rows) >= 1:
                inv_labels = [("외국인", "foreigners"), ("투신", "trusts"),
                              ("개인", "individuals"), ("법인", "corporations"),
                              ("증권사", "securities")]
                def _jpx_cell(v):
                    if v is None or v == 0:
                        return '<td class="num">—</td>'
                    c = "#26a69a" if v > 0 else "#e2574c"
                    sign = "+" if v > 0 else ""
                    return f'<td class="num" style="color:{c}">{sign}{v:,}</td>'
                wk_hdrs = ""
                for i, r in enumerate(jpx_rows[:4]):
                    d = r.get("date", f"W-{i}")
                    wk_hdrs += f'<th class="num">{esc(str(d)[:10])}</th>'
                jpx_body = ""
                for label, key in inv_labels:
                    cells = "".join(_jpx_cell(r.get(key)) for r in jpx_rows[:4])
                    jpx_body += f"<tr><td>{label}</td>{cells}</tr>\n"
                flow_pane = f"""<div class="si-pane" id="si-flow">
  <div class="si-section">
    <div class="si-section-title">JPX 주간 투자주체별 수급 (百万円 · 시장 전체)</div>
    <table class="si-table"><thead><tr><th>주체</th>{wk_hdrs}</tr></thead><tbody>{jpx_body}</tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">※ 시장 전체 집계 (종목별 아님). 출처: JPX 投資部門別 売買状況</div>
  </div>
</div>"""
        except Exception as exc:
            log.info("detail: jpx flow: %s", exc)

    # ── TW: TWSE/TPEx 三大法人 (종목별 당일+5일) ─────────────────
    if is_tw and _live and not flow_pane:
        try:
            from bot.twse_flow_client import fetch_institutional_flow
            tw_flow = fetch_institutional_flow(ticker)
            if tw_flow:
                tw_today = tw_flow.get("today") or {}
                tw_5d = tw_flow.get("5d") or {}
                def _tw_cell(v):
                    if v is None or v == 0:
                        return '<td class="num">—</td>'
                    c = "#26a69a" if v > 0 else "#e2574c"
                    sign = "+" if v > 0 else ""
                    return f'<td class="num" style="color:{c}">{sign}{v:,}</td>'
                tw_labels = [("외자 (外資)", "foreign"), ("투신 (投信)", "trust"),
                             ("자영상 (自營商)", "dealer"), ("합계", "total")]
                tw_body = ""
                for label, key in tw_labels:
                    tw_body += f"<tr><td>{label}</td>{_tw_cell(tw_today.get(key))}{_tw_cell(tw_5d.get(key))}</tr>\n"
                flow_pane = f"""<div class="si-pane" id="si-flow">
  <div class="si-section">
    <div class="si-section-title">三大法人 매매동향 (주, 종목별)</div>
    <table class="si-table"><thead><tr><th>주체</th><th class="num">당일</th><th class="num">5일 누적</th></tr></thead><tbody>{tw_body}</tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">출처: {'TWSE' if ticker.upper().endswith('.TW') else 'TPEx'} 三大法人買賣超日報</div>
  </div>
</div>"""
        except Exception as exc:
            log.info("detail: twse flow %s: %s", ticker, exc)

    # ── US: Options IV/PCR + Form 4 Insider + Finnhub Insider Sentiment
    if is_us and _live and not flow_pane:
        us_flow_parts: list[str] = []
        # Options signals
        try:
            from bot.options_client import get_options_signals
            opts = get_options_signals(ticker)
            if opts:
                o_rows = ""
                iv = opts.get("iv_atm")
                if iv is not None:
                    dte = opts.get("dte", "?")
                    o_rows += f'<tr><td>ATM IV (front)</td><td class="num">{iv:.1f}%</td><td>DTE {dte}</td></tr>\n'
                iv30 = opts.get("iv_atm_30d")
                if iv30 is not None:
                    o_rows += f'<tr><td>ATM IV (~30d)</td><td class="num">{iv30:.1f}%</td><td></td></tr>\n'
                pcr_v = opts.get("pcr_volume")
                if pcr_v is not None:
                    pcr_c = "#e2574c" if pcr_v > 1.0 else "#26a69a" if pcr_v < 0.7 else ""
                    pcr_s = f' style="color:{pcr_c}"' if pcr_c else ""
                    o_rows += f'<tr><td>P/C Volume Ratio</td><td class="num"{pcr_s}>{pcr_v:.2f}</td><td>{"🐻 bearish" if pcr_v > 1.0 else "🐂 bullish" if pcr_v < 0.7 else ""}</td></tr>\n'
                pcr_oi = opts.get("pcr_oi")
                if pcr_oi is not None:
                    o_rows += f'<tr><td>P/C OI Ratio</td><td class="num">{pcr_oi:.2f}</td><td></td></tr>\n'
                if o_rows:
                    us_flow_parts.append(f"""<div class="si-section">
      <div class="si-section-title">옵션 시장 신호</div>
      <table class="si-table"><thead><tr><th>지표</th><th class="num">값</th><th>비고</th></tr></thead><tbody>{o_rows}</tbody></table>
    </div>""")
        except Exception as exc:
            log.info("detail: options %s: %s", ticker, exc)

        # Form 4 insider trades
        try:
            from bot.edgar_client import get_recent_form4
            form4 = get_recent_form4(ticker, days=60, top_n=5)
            if form4:
                f4_rows = ""
                for f in form4:
                    f_name = esc(str(f.get("reporter_name", "—")))
                    f_title = esc(str(f.get("title", "")))
                    txns = f.get("transactions", [])
                    if not txns:
                        continue
                    for tx in txns[:2]:
                        code_t = tx.get("code", "")
                        label_t = tx.get("label", code_t)
                        shares_t = tx.get("shares", 0)
                        price_t = tx.get("price")
                        dt_t = esc(str(tx.get("date", "")))
                        tc = "#26a69a" if code_t == "P" else "#e2574c" if code_t == "S" else ""
                        ts = f' style="color:{tc}"' if tc else ""
                        sh_str = f"{int(shares_t):,}" if shares_t else "—"
                        pr_str = f"${price_t:,.2f}" if price_t else "—"
                        f4_rows += f'<tr><td>{f_name}</td><td>{esc(f_title[:20])}</td><td{ts}>{esc(label_t)}</td><td class="num">{sh_str}</td><td class="num">{pr_str}</td><td>{dt_t}</td></tr>\n'
                if f4_rows:
                    us_flow_parts.append(f"""<div class="si-section">
      <div class="si-section-title">내부자 거래 (SEC Form 4 · 60일)</div>
      <table class="si-table"><thead><tr><th>이름</th><th>직위</th><th>거래</th><th class="num">주식수</th><th class="num">가격</th><th>날짜</th></tr></thead><tbody>{f4_rows}</tbody></table>
    </div>""")
        except Exception as exc:
            log.info("detail: form4 %s: %s", ticker, exc)

        # Finnhub insider sentiment
        try:
            from bot.finnhub_client import fetch_insider_sentiment, finnhub_key_ready
            if finnhub_key_ready():
                isent = fetch_insider_sentiment(ticker)
                if isent and isent.get("data"):
                    is_rows = ""
                    for m in isent["data"][:6]:
                        month = esc(str(m.get("month", "")))
                        mspr = m.get("mspr", 0)
                        chg = m.get("change", 0)
                        mc = "#26a69a" if mspr > 0 else "#e2574c" if mspr < 0 else ""
                        ms = f' style="color:{mc}"' if mc else ""
                        is_rows += f'<tr><td>{month}</td><td class="num"{ms}>{mspr:+.2f}</td><td class="num">{chg:+d}</td></tr>\n'
                    if is_rows:
                        us_flow_parts.append(f"""<div class="si-section">
      <div class="si-section-title">내부자 심리 (MSPR · Finnhub)</div>
      <table class="si-table"><thead><tr><th>월</th><th class="num">MSPR</th><th class="num">건수 변화</th></tr></thead><tbody>{is_rows}</tbody></table>
      <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">MSPR: Monthly Share Purchase Ratio — 양수=순매수, 음수=순매도</div>
    </div>""")
        except Exception as exc:
            log.info("detail: finnhub insider %s: %s", ticker, exc)

        if us_flow_parts:
            flow_pane = '<div class="si-pane" id="si-flow">\n  ' + "\n  ".join(us_flow_parts) + "\n</div>"

    # ── 리스크 pane (lockup + dilution + CN ST/停牌) ──────────────
    risk_parts: list[str] = []

    # KR lockup releases
    lockup = kr.get("lockup_releases", [])
    if lockup:
        lu_rows = ""
        for lu in lockup:
            rd = esc(str(lu.get("release_date", "—")))
            sh = lu.get("shares")
            sh_str = f"{int(sh):,}" if sh else "—"
            rem = lu.get("remaining")
            rem_str = f"{int(rem):,}" if rem else "—"
            reason = esc(str(lu.get("reason", "—")))
            lu_rows += f"<tr><td>{rd}</td><td class='num'>{sh_str}</td><td class='num'>{rem_str}</td><td>{reason}</td></tr>\n"
        risk_parts.append(f"""<div class="si-section">
      <div class="si-section-title">📌 의무보호예수 해제 예정</div>
      <table class="si-table"><thead><tr><th>해제일</th><th class="num">반환주식</th><th class="num">잔량</th><th>사유</th></tr></thead><tbody>{lu_rows}</tbody></table>
    </div>""")

    # KR dilution events
    dilution = kr.get("dilution_events", [])
    if dilution:
        dl_rows = ""
        for de in dilution:
            kind = esc(str(de.get("kind", "—")))
            dd = esc(str(de.get("date", "—")))
            ns = de.get("new_shares")
            ns_str = f"{int(ns):,}" if ns else "—"
            pr = de.get("price")
            pr_str = f"₩{int(pr):,}" if pr else "—"
            dl_rows += f"<tr><td>{kind}</td><td>{dd}</td><td class='num'>{ns_str}</td><td class='num'>{pr_str}</td></tr>\n"
        risk_parts.append(f"""<div class="si-section">
      <div class="si-section-title">📉 잠재 희석 이벤트 (CB/BW)</div>
      <table class="si-table"><thead><tr><th>종류</th><th>날짜</th><th class="num">신주수</th><th class="num">행사가</th></tr></thead><tbody>{dl_rows}</tbody></table>
    </div>""")

    # KR KRX 시장경보 (거래정지/관리종목/투자경고/단기과열)
    kr_alert = kr.get("market_alert", {})
    if kr_alert.get("suspended"):
        risk_parts.append('<div style="background:#f8d7da;color:#721c24;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">🚫 거래정지 — 현재 매매 불가</div>')
    if kr_alert.get("admin"):
        risk_parts.append('<div style="background:#f8d7da;color:#721c24;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">⚠️ 관리종목 지정 — 상장폐지 사유 해당, 투자 주의</div>')
    wl = kr_alert.get("warning_level", "")
    if wl:
        wl_colors = {"위험": ("#f8d7da", "#721c24"), "경고": ("#fff3cd", "#856404"), "주의": ("#fff3cd", "#856404")}
        bg, fg = wl_colors.get(wl, ("#fff3cd", "#856404"))
        risk_parts.append(f'<div style="background:{bg};color:{fg};padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">⚠️ 투자{wl} — KRX 시장경보 지정</div>')
    if kr_alert.get("overheating"):
        risk_parts.append('<div style="background:#fff3cd;color:#856404;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">🔥 단기과열 종목 — 급등/급락 주의</div>')

    # CN ST/停牌 risk status
    cn_risk = mkt.get("risk_status", {}) if is_cn else {}
    if cn_risk.get("is_st"):
        risk_parts.append('<div style="background:#fff3cd;color:#856404;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">⚠️ ST/＊ST 지정 — 2년+ 연속 적자, 일일 한도 ±5%, 퇴출 위험</div>')
    if cn_risk.get("is_suspended"):
        risk_parts.append('<div style="background:#f8d7da;color:#721c24;padding:12px;border-radius:8px;margin-bottom:10px;font-weight:600">🚫 거래정지 (停牌) — 현재 매매 불가</div>')

    risk_pane = ""
    if risk_parts:
        risk_pane = '<div class="si-pane" id="si-risk">\n  ' + "\n  ".join(risk_parts) + "\n</div>"

    # ── 배당 이력 (밸류에이션 pane 하단에 추가) ────────────────────
    divs = si.get("dividends", [])
    div_html = ""
    if divs:
        div_rows = ""
        for dv in divs:
            d_date = esc(str(dv.get("date", "—")))
            d_amt = dv.get("amount")
            if d_amt is not None:
                d_fmt = f"{d_amt:,.0f}" if currency in _0dec else f"{d_amt:,.2f}"
                d_str = f"{csym}{d_fmt}"
            else:
                d_str = "—"
            div_rows += f"<tr><td>{d_date}</td><td class='num'>{d_str}</td></tr>\n"
        div_html = f"""<div class="si-section">
    <div class="si-section-title">배당 이력</div>
    <table class="si-table"><thead><tr><th>배당락일</th><th class="num">주당배당</th></tr></thead><tbody>{div_rows}</tbody></table>
  </div>"""

    # ── US EDGAR financials (밸류에이션 하단) ──────────────────────
    us = si.get("us", {})
    us_xbrl_html = ""
    us_xbrl = us.get("xbrl", {})
    if us_xbrl:
        xb_rows = ""
        label_map = {"revenue": "매출", "net_income": "순이익", "eps_diluted": "EPS (희석)",
                     "op_cash_flow": "영업현금흐름", "assets": "자산총계", "liabilities": "부채총계",
                     "equity": "자본총계", "shares": "발행주식수"}
        for key, label in label_map.items():
            m = us_xbrl.get(key, {})
            ann = m.get("annual", {})
            lat = m.get("latest", {})
            unit = m.get("unit", "USD")
            def _xbrl_fmt(v, u):
                if v is None:
                    return "—"
                if isinstance(v, float):
                    if u == "shares" or key == "eps_diluted":
                        return f"{v:,.2f}"
                    if abs(v) >= 1e9:
                        return f"${v/1e9:,.2f}B"
                    if abs(v) >= 1e6:
                        return f"${v/1e6:,.1f}M"
                    return f"${v:,.0f}"
                return f"${int(v):,}" if isinstance(v, int) and abs(v) > 1000 else str(v)
            ann_str = _xbrl_fmt(ann.get("val"), unit)
            lat_str = _xbrl_fmt(lat.get("val"), unit)
            fy_str = f"FY{ann.get('fy', '')}" if ann.get("fy") else "—"
            lat_form = lat.get("form", "")
            xb_rows += f"<tr><td>{esc(label)}</td><td class='num'>{ann_str}</td><td>{fy_str}</td><td class='num'>{lat_str}</td><td>{esc(lat_form)}</td></tr>\n"
        us_xbrl_html = f"""<div class="si-section">
    <div class="si-section-title">SEC XBRL 재무 (EDGAR)</div>
    <table class="si-table"><thead><tr><th>항목</th><th class="num">연간</th><th>회계연도</th><th class="num">최근 분기</th><th>Form</th></tr></thead><tbody>{xb_rows}</tbody></table>
  </div>"""

    # US insider trades (주주 pane 하단에 추가)
    us_insider_html = ""
    us_insiders = us.get("insider_trades", [])
    if us_insiders:
        ui_rows = ""
        for it in us_insiders:
            fd = esc(str(it.get("filing_date", "—")))
            name = esc(str(it.get("reporter_name", "—")))
            title = esc(str(it.get("title", "")))
            txns = it.get("transactions", [])
            _txn_kr = {"Purchase": "매수", "Sale": "매도", "Grant": "부여",
                       "Exercise": "행사", "Conversion": "전환", "Award": "보상",
                       "Disposition": "처분", "Acquisition": "취득",
                       "Gift": "증여", "Tax": "세금 원천징수"}
            txn_strs = []
            for tx in txns[:3]:
                raw_lbl = tx.get("label", tx.get("code", ""))
                lbl = _txn_kr.get(raw_lbl, raw_lbl)
                sh = tx.get("shares")
                pr = tx.get("price")
                parts = [lbl]
                if sh:
                    parts.append(f"{int(sh):,}주")
                if pr:
                    parts.append(f"@${pr:,.2f}")
                txn_strs.append(" ".join(parts))
            txn_str = esc("; ".join(txn_strs)) if txn_strs else "—"
            ui_rows += f"<tr><td>{fd}</td><td>{name}</td><td>{title}</td><td>{txn_str}</td></tr>\n"
        us_insider_html = f"""<div class="si-section">
    <div class="si-section-title">내부자 거래 (SEC Form 4)</div>
    <table class="si-table"><thead><tr><th>제출일</th><th>성명</th><th>직위</th><th>거래 내역</th></tr></thead><tbody>{ui_rows}</tbody></table>
  </div>"""

    # ── 공시 pane (all markets — DART/EDINET/MOPS/AKShare/EDGAR) ──
    disclosures_pane = ""
    disc_source_map = {"kr": "DART", "jp": "EDINET", "tw": "MOPS",
                       "cn": "AKShare", "us": "SEC"}
    disc_source = disc_source_map.get("kr" if is_kr else "jp" if is_jp else
                                      "tw" if is_tw else "cn" if is_cn else "us", "")
    all_disclosures = mkt.get("disclosures", [])
    if all_disclosures:
        d_rows = ""
        for disc in all_disclosures:
            d_date = esc(str(disc.get("date", "—")))
            d_title = esc(str(disc.get("title") or disc.get("subject") or disc.get("description") or "—"))
            d_reporter = esc(str(disc.get("reporter") or disc.get("filer_name") or disc.get("doc_type_label") or ""))
            d_url = disc.get("url", "")
            title_html = f'<a href="{esc(d_url)}" target="_blank" rel="noopener">{d_title}</a>' if d_url else d_title
            d_rows += f"<tr><td>{d_date}</td><td>{title_html}</td><td>{d_reporter}</td></tr>\n"
        disclosures_pane = f"""<div class="si-pane" id="si-disclosures">
  <div class="si-section">
    <div class="si-section-title">공시 ({esc(disc_source)}{' · JP 6개월' if is_jp else ' · 1년'})</div>
    <table class="si-table">
      <thead><tr><th>날짜</th><th>제목</th><th>출처</th></tr></thead>
      <tbody>{d_rows}</tbody>
    </table>
  </div>
</div>"""

    # ── 이벤트 타임라인 pane ────────────────────────────────────
    timeline_events: list[tuple[str, str, str, str, str, str]] = []
    # (date, type_label, importance, title, meta, url)

    # Earnings
    for e in si.get("earnings_history", []):
        d = e.get("date", "")
        surprise = e.get("Surprise(%)")
        eps_act = e.get("Reported EPS")
        imp = "high" if surprise is not None and abs(surprise) > 5 else "medium"
        s_str = f" (서프라이즈 {surprise:+.1f}%)" if surprise is not None else ""
        title = f"EPS {eps_act:.2f}{s_str}" if eps_act is not None else "실적 발표"
        timeline_events.append((d, "실적", imp, title, "", ""))

    # Research actions
    for u in si.get("upgrades_downgrades", []):
        d = u.get("date", "")
        firm = u.get("Firm", "")
        action = u.get("Action", "")
        to_g = u.get("ToGrade", "")
        from_g = u.get("FromGrade", "")
        imp = "high" if action in ("upgrade", "downgrade") else "low"
        change = f"{from_g} → {to_g}" if from_g else to_g
        title = f"{firm}: {change}"
        timeline_events.append((d, "리서치", imp, title, action, ""))

    # Disclosures (all markets)
    _high_kw = ("유상증자", "무상증자", "합병", "분할", "감자", "상장폐지", "거래정지",
                "株式分割", "株式併合", "上場廃止", "增資", "減資", "合併",
                "ST", "停牌", "delisting", "merger", "acquisition")
    _med_kw = ("분기보고서", "사업보고서", "반기보고서", "주요사항",
               "四半期", "有価証券", "決算短信", "重大訊息", "年度報告", "季度報告",
               "10-K", "10-Q", "8-K", "annual", "quarterly")
    for disc in mkt.get("disclosures", []):
        d = disc.get("date", "")
        title = disc.get("title") or disc.get("subject") or disc.get("description") or ""
        url = disc.get("url", "")
        imp = "high" if any(kw in title for kw in _high_kw) else \
              "medium" if any(kw in title for kw in _med_kw) else "low"
        timeline_events.append((d, "공시", imp, title, disc.get("reporter") or disc.get("filer_name") or "", url))

    # News
    for n in si.get("news", []):
        d = n.get("date", "")
        title = n.get("title", "")
        url = n.get("link", "")
        timeline_events.append((d, "뉴스", "info", title, n.get("publisher", ""), url))

    # Sort by date descending
    timeline_events.sort(key=lambda x: x[0], reverse=True)

    if timeline_events:
        tl_items = ""
        for d, type_label, imp, title, meta, url in timeline_events:
            d_esc = esc(d)
            title_esc = esc(title)
            title_html = f'<a href="{esc(url)}" target="_blank" rel="noopener">{title_esc}</a>' if url else title_esc
            badge_cls = imp
            badge_map = {"high": "높음", "medium": "보통", "low": "낮음", "info": "참고"}
            badge_label = badge_map.get(imp, "")
            meta_esc = esc(meta)
            meta_html = f' <span class="si-tl-meta">{meta_esc}</span>' if meta else ""
            tl_items += f"""<div class="si-tl-item">
  <div class="si-tl-date">{d_esc}</div>
  <div class="si-tl-body">
    <div class="si-tl-title"><span class="si-tl-type">{esc(type_label)}</span>{title_html}<span class="si-tl-badge {badge_cls}">{badge_label}</span></div>
    {meta_html}
  </div>
</div>\n"""
        timeline_pane = f"""<div class="si-pane" id="si-timeline">
  <div class="si-section">
    <div class="si-section-title">전체 이벤트 타임라인</div>
    {tl_items}
  </div>
</div>"""
    else:
        timeline_pane = """<div class="si-pane" id="si-timeline">
  <div class="si-empty">이벤트 데이터가 없습니다.</div>
</div>"""

    # ── Tab switching JS ────────────────────────────────────────
    tab_js = """<script>
(function(){
  var bar = document.getElementById('si-tab-bar');
  if (!bar) return;
  bar.addEventListener('click', function(e){
    var btn = e.target.closest('.si-tab');
    if (!btn) return;
    var target = btn.getAttribute('data-pane');
    if (!target) return;
    var tabs = bar.querySelectorAll('.si-tab');
    var panes = document.querySelectorAll('.si-pane');
    for (var i = 0; i < tabs.length; i++) tabs[i].classList.remove('active');
    for (var i = 0; i < panes.length; i++) {
      panes[i].classList.remove('active');
      panes[i].style.display = 'none';
    }
    btn.classList.add('active');
    var p = document.getElementById(target);
    if (p) { p.classList.add('active'); p.style.display = 'block'; }
  });
})();
</script>"""

    # ── assemble deferred panes (depend on variables defined above) ──
    valuation_pane = f"""<div class="si-pane" id="si-valuation">
  {w52_bar_html}
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
    <div class="si-section">
      <div class="si-section-title">밸류에이션 멀티플</div>
      <table class="si-table"><thead><tr><th>지표</th><th class="num">값</th></tr></thead><tbody>{val_multiples}</tbody></table>
    </div>
    <div class="si-section">
      <div class="si-section-title">주당 지표</div>
      <table class="si-table"><thead><tr><th>지표</th><th class="num">값</th></tr></thead><tbody>{val_per_share}</tbody></table>
    </div>
  </div>
  {kr_fin_ts_html}
  {us_xbrl_html}
  {div_html}
</div>"""

    # ── 재무제표 pane (IS / BS / CF + 수익성 차트) ──────────────────
    financials_pane = ""
    fins = si.get("financials", {})
    if fins:
        def _fin_table(stmt_data: dict, key_items: list[str], label: str) -> str:
            """Render one financial statement sub-table."""
            annual = stmt_data.get("annual", [])
            quarterly = stmt_data.get("quarterly", [])
            if not annual and not quarterly:
                return ""
            parts: list[str] = []
            for view_label, rows in (("연간", annual), ("분기", quarterly)):
                if not rows:
                    continue
                periods = [r.get("period", "?")[:7] for r in rows]
                hdr = "".join(f'<th class="num">{esc(p)}</th>' for p in periods)
                all_items = set()
                for r in rows:
                    all_items.update(k for k in r if k != "period")
                display_items = [i for i in key_items if i in all_items]
                extra = sorted(all_items - set(key_items))
                display_items.extend(extra)
                tbody = ""
                for item in display_items:
                    vals = ""
                    for r in rows:
                        v = r.get(item)
                        if v is not None:
                            if currency == "KRW":
                                if abs(v) >= 1e12:
                                    vs = f"{v/1e12:.1f}조"
                                elif abs(v) >= 1e8:
                                    vs = f"{v/1e8:,.0f}억"
                                elif abs(v) >= 1e4:
                                    vs = f"{v/1e4:,.0f}만"
                                else:
                                    vs = f"{v:,.0f}"
                            elif currency in ("JPY", "TWD", "HKD", "CNY"):
                                if abs(v) >= 1e12:
                                    vs = f"{v/1e12:.1f}兆"
                                elif abs(v) >= 1e8:
                                    vs = f"{v/1e8:,.0f}億"
                                elif abs(v) >= 1e4:
                                    vs = f"{v/1e4:,.0f}万"
                                else:
                                    vs = f"{v:,.0f}"
                            elif abs(v) >= 1e9:
                                vs = f"{v/1e9:.1f}B"
                            elif abs(v) >= 1e6:
                                vs = f"{v/1e6:.0f}M"
                            else:
                                vs = f"{v:,.0f}"
                            color = "#e2574c" if v < 0 else ""
                            style = f' style="color:{color}"' if color else ""
                            vals += f'<td class="num"{style}>{vs}</td>'
                        else:
                            vals += '<td class="num">—</td>'
                    tbody += f"<tr><td>{esc(_FIN_ITEM_KR.get(item, item))}</td>{vals}</tr>\n"
                parts.append(f"""<div class="si-section" style="margin-top:12px">
        <div class="si-section-title">{label} — {view_label}</div>
        <div style="overflow-x:auto"><table class="si-table"><thead><tr><th>항목</th>{hdr}</tr></thead><tbody>{tbody}</tbody></table></div>
      </div>""")
            return "\n".join(parts)

        is_items = ["Total Revenue", "Operating Revenue", "Cost Of Revenue",
                     "Gross Profit", "Operating Income", "Operating Expense",
                     "EBITDA", "EBIT", "Net Income", "Basic EPS", "Diluted EPS",
                     "Tax Provision", "Interest Expense"]
        bs_items = ["Total Assets", "Current Assets", "Cash And Cash Equivalents",
                     "Total Liabilities Net Minority Interest", "Current Liabilities",
                     "Total Debt", "Stockholders Equity", "Retained Earnings",
                     "Common Stock Equity", "Working Capital"]
        cf_items = ["Operating Cash Flow", "Capital Expenditure",
                     "Free Cash Flow", "Investing Cash Flow",
                     "Financing Cash Flow", "End Cash Position",
                     "Repurchase Of Capital Stock", "Cash Dividends Paid"]

        is_html = _fin_table(fins.get("income_statement", {}), is_items, "손익계산서")
        bs_html = _fin_table(fins.get("balance_sheet", {}), bs_items, "재무상태표")
        cf_html = _fin_table(fins.get("cash_flow", {}), cf_items, "현금흐름표")

        # Revenue / Operating / Net Income bar chart (inline SVG)
        chart_svg = ""
        is_annual = fins.get("income_statement", {}).get("annual", [])
        if is_annual and len(is_annual) >= 2:
            chart_items = []
            for r in reversed(is_annual[:4]):
                chart_items.append({
                    "period": r.get("period", "?")[:4],
                    "revenue": r.get("Total Revenue", 0) or 0,
                    "op_income": r.get("Operating Income", 0) or 0,
                    "net_income": r.get("Net Income", 0) or 0,
                })
            if any(c["revenue"] for c in chart_items):
                max_val = max(abs(c["revenue"]) for c in chart_items) or 1
                bar_w = 180 // len(chart_items)
                svg_w = bar_w * len(chart_items) * 3 + 120
                bars = ""
                labels = ""
                legend = ('<text x="10" y="14" font-size="11" fill="#999">● <tspan fill="#42a5f5">매출</tspan>'
                          ' ● <tspan fill="#66bb6a">영업이익</tspan>'
                          ' ● <tspan fill="#ffa726">순이익</tspan></text>')
                for i, c in enumerate(chart_items):
                    x_base = 40 + i * (bar_w * 3 + 16)
                    for j, (val, color) in enumerate([
                        (c["revenue"], "#42a5f5"),
                        (c["op_income"], "#66bb6a"),
                        (c["net_income"], "#ffa726"),
                    ]):
                        h = max(abs(val) / max_val * 120, 2)
                        y = 150 - h if val >= 0 else 150
                        bars += f'<rect x="{x_base + j * bar_w}" y="{y}" width="{bar_w - 2}" height="{h}" fill="{color}" rx="2"/>\n'
                    labels += f'<text x="{x_base + bar_w}" y="170" font-size="11" fill="#999" text-anchor="middle">{c["period"]}</text>\n'
                chart_svg = f"""<div class="si-section" style="margin-top:12px">
        <div class="si-section-title">수익성 추이</div>
        <svg width="{svg_w}" height="185" style="display:block;margin:auto">
          {legend}
          <line x1="38" y1="150" x2="{svg_w - 10}" y2="150" stroke="#555" stroke-width="1"/>
          {bars}
          {labels}
        </svg>
      </div>"""

                # YoY growth rates
                growth_rows = ""
                for i in range(1, len(chart_items)):
                    prev = chart_items[i - 1]
                    cur = chart_items[i]
                    period = cur["period"]
                    cells = ""
                    for key in ("revenue", "op_income", "net_income"):
                        p = prev[key]
                        c = cur[key]
                        if p and p != 0:
                            g = (c - p) / abs(p) * 100
                            color = "#26a69a" if g >= 0 else "#e2574c"
                            sign = "+" if g >= 0 else ""
                            cells += f'<td class="num" style="color:{color}">{sign}{g:.1f}%</td>'
                        else:
                            cells += '<td class="num">—</td>'
                    growth_rows += f"<tr><td>{period}</td>{cells}</tr>\n"
                if growth_rows:
                    chart_svg += f"""<div class="si-section" style="margin-top:12px">
        <div class="si-section-title">YoY 성장률</div>
        <table class="si-table"><thead><tr><th>연도</th><th class="num">매출</th><th class="num">영업이익</th><th class="num">순이익</th></tr></thead><tbody>{growth_rows}</tbody></table>
      </div>"""

        financials_pane = f"""<div class="si-pane" id="si-financials">
  {chart_svg}
  {is_html}
  {bs_html}
  {cf_html}
</div>"""

    # ── 동종비교 (Peer Comps) pane ────────────────────────────────
    peers_pane = ""
    peer_comps = si.get("peer_comps", [])
    if peer_comps:
        pc_rows = ""
        for pc in peer_comps:
            is_subj = pc.get("is_subject")
            style = ' style="background:rgba(66,165,245,0.12);font-weight:600"' if is_subj else ""
            name = esc(pc.get("name", "?"))
            ptk = esc(pc.get("ticker", ""))
            mc = pc.get("market_cap")
            peer_cur = pc.get("currency", "")
            if not peer_cur:
                _psuf = ptk.rsplit(".", 1)[-1] if "." in ptk else ""
                peer_cur = {"KS": "KRW", "KQ": "KRW", "T": "JPY", "TW": "TWD",
                            "HK": "HKD", "SS": "CNY", "SZ": "CNY"}.get(_psuf, "USD")
            mc_str = _fmt_mcap(mc, _currency_sym(peer_cur), peer_cur) if mc else "—"
            def _pv(k):
                v = pc.get(k)
                return f"{v:.1f}" if v else "—"
            _pc_dy = _safe_dy_pct(pc.get("dividendYield"), pc.get("dividendRate"), pc.get("currentPrice"))
            dy_str = f"{_pc_dy:.1f}%" if _pc_dy is not None else "—"
            pc_rows += f'<tr{style}><td>{name}</td><td>{ptk}</td><td class="num">{mc_str}</td>'
            pc_rows += f'<td class="num">{_pv("trailingPE")}</td><td class="num">{_pv("forwardPE")}</td>'
            pc_rows += f'<td class="num">{_pv("priceToBook")}</td><td class="num">{_pv("priceToSalesTrailing12Months")}</td>'
            pc_rows += f'<td class="num">{_pv("enterpriseToEbitda")}</td><td class="num">{dy_str}</td></tr>\n'
        peers_pane = f"""<div class="si-pane" id="si-peers">
  <div class="si-section">
    <div class="si-section-title">동종업계 밸류에이션 비교</div>
    <div style="overflow-x:auto">
    <table class="si-table">
      <thead><tr><th>회사</th><th>티커</th><th class="num">시총</th><th class="num">PER</th><th class="num">선행 PER</th><th class="num">PBR</th><th class="num">PSR</th><th class="num">EV/EBITDA</th><th class="num">배당</th></tr></thead>
      <tbody>{pc_rows}</tbody>
    </table>
    </div>
  </div>
</div>"""

    # JP EDINET major holders (5%+ 大量保有)
    jp_holders_html = ""
    jp_holders = si.get("jp", {}).get("major_holders", []) if is_jp else []
    if jp_holders:
        jh_rows = ""
        for jh in jp_holders:
            jd = esc(str(jh.get("date", "—")))
            jfn = esc(str(jh.get("filer_name", "—")))
            jdesc = esc(str(jh.get("description", "")))
            jh_rows += f"<tr><td>{jd}</td><td>{jfn}</td><td>{jdesc}</td></tr>\n"
        jp_holders_html = f"""<div class="si-section">
    <div class="si-section-title">5%+ 대량보유 (EDINET)</div>
    <table class="si-table">
      <thead><tr><th>제출일</th><th>보고자</th><th>내용</th></tr></thead>
      <tbody>{jh_rows}</tbody>
    </table>
  </div>"""

    # TW MOPS insider holdings (內部人持股)
    tw_insiders_html = ""
    tw_insiders = si.get("tw", {}).get("insider_holdings", []) if is_tw else []
    if tw_insiders:
        ti_rows = ""
        for ti in tw_insiders:
            tn = esc(str(ti.get("name", "—")))
            tr_ = esc(str(ti.get("role", "")))
            ts = ti.get("shares")
            ts_str = f"{int(ts):,}" if ts else "—"
            tp = ti.get("pct")
            tp_str = f"{tp:.2f}%" if tp else "—"
            ti_rows += f"<tr><td>{tn}</td><td>{tr_}</td><td class='num'>{ts_str}</td><td class='num'>{tp_str}</td></tr>\n"
        tw_insiders_html = f"""<div class="si-section">
    <div class="si-section-title">내부자 지분 (MOPS)</div>
    <table class="si-table">
      <thead><tr><th>성명</th><th>직위</th><th class="num">보유주식</th><th class="num">지분율</th></tr></thead>
      <tbody>{ti_rows}</tbody>
    </table>
  </div>"""

    # CN AKShare major holders (主要流通股东)
    cn_holders_html = ""
    cn_holders = si.get("cn", {}).get("major_holders", []) if is_cn else []
    if cn_holders:
        ch_rows = ""
        for ch in cn_holders:
            cn_ = esc(str(ch.get("name", "—")))
            cs = ch.get("shares")
            cs_str = f"{int(cs):,}" if cs else "—"
            cp = ch.get("pct")
            cp_str = f"{cp:.2f}%" if cp else "—"
            cnat = esc(str(ch.get("nature", "")))
            ch_rows += f"<tr><td>{cn_}</td><td class='num'>{cs_str}</td><td class='num'>{cp_str}</td><td>{cnat}</td></tr>\n"
        cn_holders_html = f"""<div class="si-section">
    <div class="si-section-title">주요 유통주주 (AKShare)</div>
    <table class="si-table">
      <thead><tr><th>주주명</th><th class="num">보유주식</th><th class="num">지분율</th><th>성격</th></tr></thead>
      <tbody>{ch_rows}</tbody>
    </table>
  </div>"""

    # Short Interest visualization
    short_html = ""
    shares_short = si.get("shares_short")
    short_ratio = si.get("short_ratio")
    shares_out = si.get("shares_outstanding")
    if shares_short:
        si_pct = (shares_short / shares_out * 100) if shares_out and shares_out > 0 else None
        si_pct_str = f"{si_pct:.2f}%" if si_pct else "—"
        sr_str = f"{short_ratio:.1f}일" if short_ratio else "—"
        si_bar = ""
        if si_pct:
            bar_w = min(si_pct * 5, 100)
            bar_color = "#e2574c" if si_pct > 10 else ("#ffa726" if si_pct > 5 else "#66bb6a")
            si_bar = f'''<div style="margin-top:6px;height:10px;background:#333;border-radius:5px;overflow:hidden;max-width:300px">
          <div style="height:100%;width:{bar_w:.0f}%;background:{bar_color};border-radius:5px"></div>
        </div>
        <div style="font-size:11px;color:#888;margin-top:2px">0% {'━' * 10} 20%+</div>'''
        short_html = f"""<div class="si-section">
    <div class="si-section-title">공매도 현황</div>
    <table class="si-table"><tbody>
      <tr><td>공매도 주식수</td><td class="num">{int(shares_short):,}</td></tr>
      <tr><td>공매도 비율</td><td class="num">{si_pct_str}</td></tr>
      <tr><td>공매도 커버일수</td><td class="num">{sr_str}</td></tr>
    </tbody></table>
    {si_bar}
  </div>"""

    # US insider trades summary (Form 4 buy/sell aggregation)
    us_insider_summary_html = ""
    us_ins = si.get("us", {}).get("insider_trades", [])
    if us_ins:
        buys = sum(1 for t_ in us_ins if t_.get("transaction_type", "").startswith(("P", "A")))
        sells = sum(1 for t_ in us_ins if t_.get("transaction_type", "").startswith(("S", "D")))
        if buys or sells:
            total = buys + sells
            buy_pct = buys / total * 100 if total else 0
            sell_pct = sells / total * 100 if total else 0
            us_insider_summary_html = f"""<div class="si-section">
    <div class="si-section-title">내부자 거래 요약 (SEC Form 4)</div>
    <div style="display:flex;gap:20px;align-items:center;margin:8px 0">
      <div style="text-align:center"><span style="font-size:24px;font-weight:700;color:#26a69a">{buys}</span><div style="font-size:12px;color:#999">매수</div></div>
      <div style="flex:1;height:12px;background:#333;border-radius:6px;overflow:hidden;display:flex">
        <div style="width:{buy_pct:.0f}%;background:#26a69a"></div>
        <div style="width:{sell_pct:.0f}%;background:#e2574c"></div>
      </div>
      <div style="text-align:center"><span style="font-size:24px;font-weight:700;color:#e2574c">{sells}</span><div style="font-size:12px;color:#999">매도</div></div>
    </div>
  </div>"""

    holders_pane = f"""<div class="si-pane" id="si-holders">
  <div class="si-section">
    <div class="si-section-title">주요 기관</div>
    {holder_summary}
    {holders_table}
  </div>
  {short_html}
  {us_insider_summary_html}
  {kr_foreign_html}
  {kr_insider_html}
  {kr_minority_html}
  {kr_affiliates_html}
  {kr_affiliates_invest_html}
  {us_insider_html}
  {jp_holders_html}
  {tw_insiders_html}
  {cn_holders_html}
</div>"""

    # Return a dict with separate pieces so _render_detail can wrap
    # the chart/summary/report inside the overview pane. The per-pane
    # map lets build_live_quote(full=True) hand back just the heavy,
    # filing-/daily-cadence panes for the client to swap in.
    return {
        "header": header,
        "tabs": tabs_html,
        "tab_js": tab_js,
        "other_panes": company_pane + "\n" + consensus_pane + "\n" +
                       valuation_pane + "\n" + financials_pane + "\n" +
                       peers_pane + "\n" + earnings_pane + "\n" +
                       research_pane + "\n" + holders_pane + "\n" +
                       flow_pane + "\n" + disclosures_pane + "\n" +
                       risk_pane + "\n" + news_pane + "\n" + timeline_pane,
        "panes": {
            "si-financials": financials_pane,
            "si-peers": peers_pane,
            "si-earnings": earnings_pane,
            "si-research": research_pane,
            "si-holders": holders_pane,
            "si-flow": flow_pane,
            "si-disclosures": disclosures_pane,
            "si-risk": risk_pane,
            "si-news": news_pane,
        },
    }


# Live-quote overlay: on page load, fetch fresh numbers and drop them
# into the [data-q] cells the renderer tagged. LIGHT (price-derived) is
# fast; FULL swaps the heavy filing-/daily-cadence panes when ready. Any
# failure leaves the stored snapshot untouched (graceful — never blanks).
_QUOTE_JS = r"""
(function(){
  if (typeof NOAH_TICKER === 'undefined' || !NOAH_TICKER) return;
  var base = (typeof NOAH_BASE !== 'undefined') ? NOAH_BASE : '../';
  var lastFmt = null;
  function applyFmt(fmt){
    if (!fmt) return;
    for (var k in fmt){
      if (!fmt.hasOwnProperty(k)) continue;
      var els = document.querySelectorAll('[data-q="' + k + '"]');
      for (var i = 0; i < els.length; i++) els[i].textContent = fmt[k];
    }
  }
  function applyPos(pos){
    if (!pos) return;
    function set(sel, prop, val){ var e = document.querySelector(sel); if (e) e.style[prop] = val; }
    if (pos.w52_pct != null){ set('[data-qmark="w52"]','left',pos.w52_pct+'%'); set('[data-qfill="w52"]','width',pos.w52_pct+'%'); }
    if (pos.tgt_pct != null) set('[data-qmark="tgt"]','left',pos.tgt_pct+'%');
    if (pos.tgt_mean_pct != null){
      var f = document.querySelector('[data-qfill="tgt"]');
      if (f){ f.style.width = pos.tgt_mean_pct + '%'; if (pos.tgt_fill_pos != null) f.style.background = pos.tgt_fill_pos ? '#26a69a' : '#e2574c'; }
    }
    if (pos.upside_pos != null){ var u = document.querySelector('[data-q="upside"]'); if (u) u.style.color = pos.upside_pos ? '#26a69a' : '#e2574c'; }
  }
  function badge(meta, ok){
    var b = document.getElementById('q-badge'); if (!b) return;
    if (!ok){ b.textContent = '⏸ 종가 기준'; b.title = '실시간 갱신 일시 불가 — 분석 시점 종가 표시'; return; }
    var src = (meta && meta.source) || 'yfinance';
    var dl = (meta && meta.delayed) ? ' · ~15분 지연' : '';
    var ts = (meta && meta.ts) ? (' · ' + meta.ts + ' KST') : '';
    b.textContent = '🟢 라이브 · ' + src + dl + ts;
  }
  fetch(base + 'api/quote?ticker=' + encodeURIComponent(NOAH_TICKER), {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok || !j.quote){ badge(null, false); return; }
      lastFmt = j.quote.fmt;
      applyFmt(j.quote.fmt); applyPos(j.quote.pos); badge(j.quote.meta, true);
    })
    .catch(function(){ badge(null, false); });
  fetch(base + 'api/quote?ticker=' + encodeURIComponent(NOAH_TICKER) + '&full=1', {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok || !j.quote || !j.quote.panes) return;
      var p = j.quote.panes;
      for (var id in p){
        if (!p.hasOwnProperty(id) || !p[id]) continue;
        var el = document.getElementById(id);
        if (!el) continue;
        var wasActive = el.classList.contains('active');
        el.outerHTML = p[id];
        if (wasActive){ var n = document.getElementById(id); if (n){ n.classList.add('active'); n.style.display = 'block'; } }
      }
      if (lastFmt) applyFmt(lastFmt);
    })
    .catch(function(){});
})();
"""


def _render_detail(rec: dict, analysis_markers: list[dict] | None = None) -> str:
    ticker = rec.get("ticker", "?")
    date = rec.get("trade_date", "")
    analyzed_at = (rec.get("analyzed_at") or "")[:16].replace("T", " ")
    elapsed = float(rec.get("elapsed_sec", 0) or 0)
    # Per-analysis Gemini cost (incl. this run's chart disclosure-title
    # translation), stamped by archive.save_analysis. Older records lack the
    # field → cost line is omitted (only stamped going forward).
    try:
        _cost_krw = int(round(float(rec.get("cost_krw", 0) or 0)))
    except (TypeError, ValueError):
        _cost_krw = 0
    cost_part = f" · 비용: ₩{_cost_krw:,}" if _cost_krw > 0 else ""
    rating = _extract(_RATING_RE, rec.get("summary", "")) or "?"
    summary = rec.get("summary", "") or ""
    full = rec.get("full_report", "") or ""

    # Title / header use the Korean corp name for KR tickers so the
    # browser tab and the in-page H1 read '한솔케미칼 / 014680.KS'
    # rather than the bare numeric ticker. The bare ticker stays in
    # the meta line below for users who navigated by ticker.
    kr_name = _ticker_kr_name(ticker)
    h1_label = f"{kr_name} / {ticker}" if kr_name else ticker

    # 5-trading-day outcome — surface here on the detail page too, not
    # just on the index card list. Users navigating directly to
    # /<DATE>/<TICKER>.html (e.g. via Telegram link to '📋 전체 리포트')
    # previously had to bounce back to the index to see if their
    # analysis got an outcome. Now visible inline right under the
    # meta line. Empty string when the 5-day window hasn't elapsed
    # or auto_resolve hasn't caught up yet — same falsy-handling as
    # the card renderer.
    try:
        resolved_entry = _build_resolved_lookup().get((date, ticker))
    except Exception:
        resolved_entry = None
    outcome_html = _render_outcome_html(resolved_entry)
    chart_html = _render_chart_section(rec, analysis_markers)
    chart_scripts = (
        f'<script src="../{_LWC_LIB_NAME}"></script>\n<script>{_CHART_JS}</script>'
        if chart_html else ""
    )

    # Enrich the stock_info with missing tabs if the stored snapshot
    # predates the enrichment code. In-memory only — never writes back
    # to the JSON file.
    si = rec.get("stock_info")
    if si:
        _needs = (
            not si.get("news")
            or not si.get("financials")
            or not si.get("peer_comps")
        )
        if not _needs:
            tkr_u = (ticker or "").upper()
            if tkr_u.endswith((".KS", ".KQ")):
                _kr = si.get("kr", {})
                _needs = not _kr.get("flow") or not _kr.get("disclosures")
            else:
                _mkt_key = (
                    "jp" if tkr_u.endswith(".T") else
                    "tw" if tkr_u.endswith((".TW", ".TWO")) else
                    "cn" if tkr_u.endswith((".SS", ".SZ", ".BJ", ".HK")) else
                    "us"
                )
                _needs = not si.get(_mkt_key, {}).get("disclosures")
        # NEVER run live enrichment (news/research/consensus HTTP per ticker)
        # during a batch regen — it blocks Telegram polling and trips the
        # watchdog. The on-demand /api/quote?full=1 path (server process)
        # calls _ensure_detail_enrichment itself, so the page fills on load.
        if _needs and not _BATCH_REGEN:
            try:
                _ensure_detail_enrichment(ticker, si)
            except Exception:
                pass

    # Stock info (v3+): header cards, tab navigation, company/consensus/
    # earnings/research/holders/news panes. Graceful: older records just
    # skip the whole block.
    si_parts = _render_stock_info_html(rec)
    si_header = si_parts.get("header", "") if si_parts else ""
    si_tabs = si_parts.get("tabs", "") if si_parts else ""
    si_tab_js = si_parts.get("tab_js", "") if si_parts else ""
    si_other = si_parts.get("other_panes", "") if si_parts else ""
    has_tabs = bool(si_parts)

    # Live-quote overlay script — only when there are stock-info tabs to
    # update. Embeds the ticker as a JSON literal (safe) + the same
    # relative base the chart API uses.
    quote_script = (
        f'<script>var NOAH_TICKER={json.dumps(ticker)};'
        f'var NOAH_BASE="../";{_QUOTE_JS}</script>'
        if has_tabs else ""
    )

    # When tabs are present, the chart/summary/report go inside the
    # "종합" pane. Otherwise they render directly (pre-v3 records).
    overview_open = '<div class="si-pane active" id="si-overview">' if has_tabs else ""
    overview_close = "</div>" if has_tabs else ""

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 {_html.escape(h1_label)} ({_html.escape(date)})</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../index.html" onclick="if(history.length>1){{history.back();return false}}">← 아카이브로 돌아가기</a>
  <div class="title-row">
    <h1>📊 {_html.escape(h1_label)}</h1>
    {_badge_html(rating)}
  </div>
  <div class="meta">
    분석일: {_html.escape(date)} · 실행 시각: {_html.escape(analyzed_at)} ·
    소요: {elapsed:.1f}초{cost_part}
  </div>
  {si_header}
  {si_tabs}
  {overview_open}
  {outcome_html}
  {chart_html}
  <section class="report-section">
    <h2>📋 요약</h2>
    {_md_to_html(summary)}
  </section>
  <section class="report-section">
    <h2>📋 전체 리포트</h2>
    {_md_to_html(full)}
  </section>
  {overview_close}
  {si_other}
</div>
<script>{_DETAIL_DEEP_LINK_JS}</script>
{si_tab_js}
{chart_scripts}
{quote_script}
</body>
</html>
"""


# ─── public entry point ──────────────────────────────────────────────
def regenerate_index() -> None:
    """Scan archive dir, rewrite index.html and per-analysis detail pages.

    Called from ``bot.analyzer.analyze`` after each archive write.
    Idempotent. Safe to call repeatedly. All errors are swallowed —
    dashboard issues must never break the analysis pipeline.
    """
    global _BATCH_REGEN
    try:
        _BATCH_REGEN = True
        from bot.translate import set_cache_only
        set_cache_only(True)
        records = _load_all()
        # Newest-first: dates descending, then analyzed_at descending
        records.sort(
            key=lambda r: (r.get("trade_date", ""), r.get("analyzed_at", "")),
            reverse=True,
        )
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        # Vendor the chart library once (idempotent) so detail pages can
        # self-host it. Non-fatal: a miss just means charts don't render.
        _ensure_chart_lib()
        (ARCHIVE_ROOT / "index.html").write_text(
            _render_index(records), encoding="utf-8"
        )
        # Errors / partial-completion catalog (Option A from the spec).
        hard_failures = _read_hard_failures()
        (ARCHIVE_ROOT / "errors.html").write_text(
            _render_errors_page(records, hard_failures), encoding="utf-8"
        )
        # Past-analysis markers per ticker (track record on the chart) —
        # build once from the already-loaded records + resolved outcomes,
        # then pass the relevant slice into each detail page (avoids O(n²)).
        _resolved = _build_resolved_lookup()
        _by_ticker: dict[str, list[dict]] = {}
        for r in records:
            _by_ticker.setdefault(r.get("ticker", ""), []).append(r)
        _markers_by_ticker = {
            t: _ticker_analysis_markers(recs, _resolved)
            for t, recs in _by_ticker.items()
        }
        for rec in records:
            day = rec.get("trade_date", "")
            ticker = rec.get("ticker", "")
            if not day or not ticker:
                continue
            day_dir = ARCHIVE_ROOT / day
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / f"{ticker}.html").write_text(
                _render_detail(rec, _markers_by_ticker.get(ticker)),
                encoding="utf-8",
            )
        log.info("dashboard: regenerated with %d entries", len(records))
    except Exception as exc:
        log.warning("dashboard: regenerate failed: %s", exc)
    finally:
        _BATCH_REGEN = False
        try:
            set_cache_only(False)
        except Exception:
            pass


# ── Screener archive view ────────────────────────────────────────────────
# Separate from the per-ticker /analysis dashboard. Screener picks are
# multi-stock theme generation (6-18M thesis), so the rendering is a
# date-grouped run list with Top-3 mini-tables showing 5/15/30d outcomes
# (alpha vs sector ETF). Outcomes come from screener_memory.md via
# auto_resolve.py 's screener pass.

_SCREENER_ARCHIVE_DIR = Path.home() / ".tradingagents" / "screener_archive"
_SCREENER_MEMORY_PATH = (
    Path.home() / ".tradingagents" / "memory" / "screener_memory.md"
)

# Daily Byte (장 마감 후 KR 수급 브리프) archive — mirrors the screener
# archive layout: ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/HHMMSS_
# daily_byte.json. Rendered to daily_byte.html with the same theme + search
# + trash UX as screener.html (reuses _SCREENER_CSS).
_DAILY_BYTE_ARCHIVE_DIR = Path.home() / ".tradingagents" / "daily_byte_archive"


def _load_screener_runs() -> list[dict]:
    """Scan ~/.tradingagents/screener_archive/YYYY-MM-DD/*.json and return
    a list of run dicts (newest first). Each run has {ts, domain,
    raw_output, validated_tickers, rejected_tickers, elapsed_sec,
    cost_krw, top_3_picks, _path, _date}.

    Lazy migration (2026-05-29): old JSONs (saved before commit 01b3957)
    lack binding_constraint / top3_section / bottom_line fields, and old
    JSONs (saved before d9ddd25) carry the legacy '(Phase β · 실시간
    데이터)' suffix in their domain string. We parse sections from
    raw_output on the fly + strip the suffix at load time so the
    dashboard renders consistently for ALL runs without a separate
    migration job. Cost is negligible (~5 regex passes per file)."""
    import json as _json
    runs: list[dict] = []
    if not _SCREENER_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_SCREENER_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = json_file.name
                    # Migrate old archives without parsed sections — or
                    # re-parse if previous migration produced empty strings
                    # (regex couldn't anchor because Pro emitted <b>...</b>
                    # around the section headers). Always re-parse when the
                    # stored fields are blank but raw_output exists.
                    need_parse = (
                        rec.get("raw_output")
                        and (not rec.get("binding_constraint")
                             or not rec.get("master_table")
                             or not rec.get("top3_section")
                             or not rec.get("bottom_line"))
                    )
                    if need_parse:
                        sections = _parse_screener_sections_local(rec["raw_output"])
                        # Use parsed value when stored is empty; preserve
                        # stored value when it's non-empty (already migrated).
                        for k in ("binding_constraint", "master_table",
                                  "top3_section", "bottom_line"):
                            if not rec.get(k) and sections.get(k):
                                rec[k] = sections[k]
                    # Strip legacy Phase β suffix from domain for display
                    raw_domain = rec.get("domain", "") or ""
                    rec["domain"] = re.sub(
                        r"\s*\(Phase\s*β[^)]*\)", "", raw_domain
                    ).strip() or raw_domain
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: screener load %s failed: %s", json_file, exc)
    except Exception as exc:
        log.warning("dashboard: screener archive scan failed: %s", exc)
    return runs


def _parse_screener_sections_local(raw: str) -> dict:
    """Mirror of bot.screener._parse_screener_sections — duplicated here so
    dashboard.py doesn't import from screener.py at module-load time.
    Extracts 📍 binding constraint / 🏆 Top 3 / 💡 Bottom line.

    Tolerates Pro's `<b>...</b>` / markdown formatting around the section
    headers (2026-05-29 surfaced: Pro emits '<b>📍 현재 binding
    constraint</b>' so anchoring on plain '📍 현재 binding constraint\n'
    failed → sections came back empty, lazy migration produced nothing).
    Fix: strip HTML tags + markdown bold + markdown headers before regex."""
    out = {"binding_constraint": "", "master_table": "",
           "top3_section": "", "bottom_line": ""}
    if not raw:
        return out
    # Normalise away noise that breaks header anchoring
    clean = raw
    clean = re.sub(r"<\/?[a-zA-Z]+[^>]*>", "", clean)   # <b>, </b>, <i>, etc.
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", clean)    # **bold** → bold
    clean = re.sub(r"^#+\s*", "", clean, flags=re.MULTILINE)  # ## headers
    m = re.search(
        r"📍\s*현재\s*binding\s*constraint\s*\n+(.*?)(?=\n+(?:📊|🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["binding_constraint"] = m.group(1).strip()
    # Master Table 전체 (4단계 결과 — 검증된 모든 ticker 행)
    m = re.search(
        r"📊\s*Master\s*Table\s*\n+(.*?)(?=\n+(?:🏆|💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["master_table"] = m.group(1).strip()
    m = re.search(
        r"🏆\s*Top\s*3\s*conviction\s*picks\s*\n+(.*?)(?=\n+(?:💡|⚠️)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["top3_section"] = m.group(1).strip()
    m = re.search(
        r"💡\s*Bottom\s*line\s*\n+(.*?)(?=\n+(?:⚠️|🤖)|\Z)",
        clean, re.DOTALL,
    )
    if m: out["bottom_line"] = m.group(1).strip()
    return out


def _load_screener_outcomes() -> dict[tuple[str, str], dict]:
    """Parse screener_memory.md → dict keyed by (date, ticker). Each value
    has the TradingMemoryLog parsed entry shape — raw/alpha (5d) in the
    tag + outcomes_extra list for 15d/30d. auto_resolve.py keeps this
    current at 12h cadence."""
    out: dict[tuple[str, str], dict] = {}
    if not _SCREENER_MEMORY_PATH.exists():
        return out
    try:
        from tradingagents.agents.utils.memory import TradingMemoryLog
        cfg = {"memory_log_path": str(_SCREENER_MEMORY_PATH)}
        memory = TradingMemoryLog(cfg)
        for e in memory.load_entries():
            key = (e.get("date", ""), (e.get("ticker") or "").upper())
            if key[0] and key[1]:
                out[key] = e
    except Exception as exc:
        log.warning("dashboard: screener memory load failed: %s", exc)
    return out


def _fmt_pct_signed(s: str | None) -> str:
    """'+2.3%' → '+2.3%' (pass-through with sign coloring done via CSS class)."""
    if not s:
        return "—"
    return s.strip()


def _outcome_class(s: str | None) -> str:
    """CSS class for a return string: positive / negative / neutral."""
    if not s:
        return "neu"
    s = s.strip()
    if s.startswith("+") and not s.startswith("+0.0"):
        return "pos"
    if s.startswith("-") and not s.startswith("-0.0"):
        return "neg"
    return "neu"


def _month_kr(ym: str) -> str:
    """'YYYY-MM' → 'YYYY년 M월'. Fallback to raw on parse error."""
    try:
        y, m = ym.split("-")[:2]
        return f"{int(y)}년 {int(m)}월"
    except Exception:
        return ym


def _month_transition(
    parts: list[str], prev_month: str | None, date: str,
    this_month: str, month_counts: dict[str, int],
) -> str:
    """Emit month-group <details> open/close transitions into `parts` as a
    date loop iterates newest-first. Call once at the top of each iteration
    BEFORE the day <details>. Returns the new prev_month to thread forward.

    Wraps date groups in '📆 YYYY년 M월' collapsibles (this month open, past
    months collapsed) — universal across all date-grouped dashboards
    (사용자 2026-06-01: Daily Byte 와 동일 형식을 월 collapse 없는 모든
    대시보드에 적용). When the loop finishes call `_month_close(parts,
    prev_month)` to shut the final open month.

    month_counts: {YYYY-MM: 총 건수} precomputed by caller for the badge.
    """
    cur_month = (date or "")[:7]
    if cur_month != prev_month:
        if prev_month is not None:
            parts.append("</div></details>")  # close previous month
        m_open = " open" if cur_month == this_month else ""
        parts.append(
            f'<details class="month"{m_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_month_kr(cur_month)}</span>'
            f'<span class="count">{month_counts.get(cur_month, 0)}건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
    return cur_month


def _month_close(parts: list[str], prev_month: str | None) -> None:
    """Close the final open month group (no-op if none was opened)."""
    if prev_month is not None:
        parts.append("</div></details>")


def _render_screener_page(runs: list[dict], outcomes: dict) -> str:
    """Render screener.html — date-grouped run cards with Top-3 mini-tables
    showing 5/15/30d outcomes (alpha vs sector ETF). Self-contained HTML
    with embedded CSS matching NOAH dashboard dark-on-light palette."""
    import html as _html
    from collections import defaultdict

    # Group runs by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    # Build summary stats
    total_runs = len(runs)
    total_cost_krw = sum(r.get("cost_krw", 0) or 0 for r in runs)
    # 오늘(KST) 비용 — 다른 대시보드처럼 '오늘 / 누적' 동시 표기 (사용자
    # 2026-05-30). run 의 _date (YYYY-MM-DD, 아카이브 날짜 디렉토리) 기준.
    from datetime import datetime as _dt_tc, timezone as _tz_tc, timedelta as _td_tc
    _today_kst_str = _dt_tc.now(_tz_tc(_td_tc(hours=9))).date().isoformat()
    _month_kst_str = _today_kst_str[:7]  # 'YYYY-MM'
    today_cost_krw = sum(
        r.get("cost_krw", 0) or 0 for r in runs if r.get("_date") == _today_kst_str
    )
    month_cost_krw = sum(
        r.get("cost_krw", 0) or 0 for r in runs
        if (r.get("_date") or "").startswith(_month_kst_str)
    )
    total_picks = sum(len(r.get("top_3_picks", []) or []) for r in runs)
    resolved_count = sum(
        1 for r in runs for p in (r.get("top_3_picks") or [])
        if outcomes.get((r.get("_date", ""), (p.get("ticker") or "").upper()), {}).get("raw")
    )

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="screener_domains.html">🗂️ 도메인 목록</a>
    · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>
    · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>
  </div>
  <h1>📊 Bottleneck Screener — Archive</h1>
  <p class="sub">테마별 다종목 idea generation · 6-18M thesis (NOAH /ticker 5거래일 평가와 별개 horizon)</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 실행</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost_krw:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost_krw:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost_krw:,.0f}</div><div class="stat-l">누적 비용</div></div>
    <div class="stat"><div class="stat-v">{total_picks}</div><div class="stat-l">Top-3 picks</div></div>
    <div class="stat"><div class="stat-v">{resolved_count}</div><div class="stat-l">1m resolved</div></div>
  </div>

  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="ticker / 회사명 / 테마 / 본문 검색 (예: 변압기, 액체냉각, ETN, Eaton)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 screener 실행</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 screener 실행 기록이 없습니다. 텔레그램 채널에서
    <code>/screener</code> 를 실행하세요.
  </div>
</div>""")
        return "".join(parts)

    # Sort dates newest-first; expand only today's group by default so the
    # archive stays compact once many days accumulate (NOAH index.html
    # uses the same pattern). Pre-resolve `today` once outside the loop.
    from datetime import datetime as _dt_sc, timezone as _tz_sc, timedelta as _td_sc
    _today_kst = _dt_sc.now(_tz_sc(_td_sc(hours=9))).date().isoformat()
    _this_month = _today_kst[:7]
    _month_counts: dict[str, int] = defaultdict(int)
    for d in by_date:
        _month_counts[(d or "")[:7]] += len(by_date[d])
    _prev_month: str | None = None
    for date in sorted(by_date.keys(), reverse=True):
        _prev_month = _month_transition(
            parts, _prev_month, date, _this_month, _month_counts)
        day_open = " open" if date == _today_kst else ""
        day_count = len(by_date[date])
        parts.append(
            f'<details class="day"{day_open}>'
            f'<summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{day_count}건</span>'
            f'</summary>'
            f'<div class="day-body">'
        )
        for r in by_date[date]:
            domain = _html.escape(r.get("domain", "Unknown"))
            # Extract HH:MM from ISO 'YYYY-MM-DDTHH:MM:SS+09:00'. Previous
            # slice ts[-8:-3] returned ':00+09' (suffix of timezone), not
            # the clock — 2026-05-29 surfaced. Use 'T' split for safety.
            raw_ts = r.get("ts") or ""
            ts_clock = ""
            if "T" in raw_ts:
                ts_clock = raw_ts.split("T", 1)[1][:5]  # 'HH:MM'
            ts = _html.escape(ts_clock)
            cost = r.get("cost_krw", 0) or 0
            elapsed = r.get("elapsed_sec", 0) or 0
            validated_list = r.get("validated_tickers", []) or []
            tickers_n = len(validated_list)
            # Tooltip: full ticker list on hover over '11개 ticker' chip
            tickers_title = (
                "Master Table 종목: " + ", ".join(validated_list)
                if validated_list else "검증된 종목 없음"
            )
            picks = r.get("top_3_picks", []) or []

            # Narrative sections (binding constraint / Top 3 rationale /
            # bottom line) — collapsed by default; user clicks summary to
            # expand. NOAH index.html uses similar <details> pattern for
            # past-outcomes accordions, kept visual parity here.
            binding = (r.get("binding_constraint") or "").strip()
            master_table_txt = (r.get("master_table") or "").strip()
            top3_section_txt = (r.get("top3_section") or "").strip()
            bottom_line = (r.get("bottom_line") or "").strip()
            has_analysis = bool(binding or master_table_txt or top3_section_txt or bottom_line)
            analysis_html = ""
            if has_analysis:
                pieces = []
                if binding:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'📍 현재 binding constraint</div>'
                        f'<div class="analysis-b" data-section="binding">{_html.escape(binding)}</div></div>'
                    )
                if master_table_txt:
                    # Master Table 은 11행 처럼 길어서 nested collapsible
                    # (디폴트 접힘) — 사용자가 클릭해 모든 ticker 행 펼침.
                    # Section title `[전력: 변압기]` / `[냉각: 액체냉각 CDU]`
                    # 같이 대괄호로 감싼 줄을 굵은 글씨로 강조 — 11개 ticker
                    # 가 4-6개 테마 그룹으로 시각적 구분되게.
                    mt_safe = _html.escape(master_table_txt)
                    mt_bolded = re.sub(
                        r"(^|\n)(\[[^\]\n]+\])",
                        r'\1<b class="mt-section">\2</b>',
                        mt_safe,
                    )
                    pieces.append(
                        f'<details class="analysis-mt"><summary>'
                        f'📊 Master Table 펼치기 (검증된 {tickers_n}개 ticker 전체 — 테마별 Tier A/B/C 신호 + 가격 반영도 + catalyst + kill trigger)'
                        f'</summary>'
                        f'<div class="analysis-sec"><div class="analysis-b" data-section="master_table">'
                        f'{mt_bolded}</div></div>'
                        f'</details>'
                    )
                if top3_section_txt:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'🏆 Top 3 conviction picks (추천 근거)</div>'
                        f'<div class="analysis-b" data-section="top3">{_html.escape(top3_section_txt)}</div></div>'
                    )
                if bottom_line:
                    pieces.append(
                        f'<div class="analysis-sec"><div class="analysis-h">'
                        f'💡 Bottom line</div>'
                        f'<div class="analysis-b" data-section="bottom">{_html.escape(bottom_line)}</div></div>'
                    )
                analysis_html = (
                    f'<details class="analysis">'
                    f'<summary>📖 분석 내용 펼치기 (binding · Master Table · Top 3 · bottom line)</summary>'
                    + "".join(pieces) +
                    f'</details>'
                )

            filename = _html.escape(r.get("_filename", ""))
            # Build per-line snippet index: search matches show the
            # specific line containing the term, with surrounding context
            # tightly capped (200-char single line) so the snippet panel
            # stays readable. SV dashboard pattern — typing '변압기'
            # surfaces the master-table row that mentions it; clicking the
            # snippet jumps to the card and opens it in place. Falls back
            # to whole-card filter via card-level hay below when content
            # is empty (legacy archive entries without parsed sections).
            def _lines(label: str, text: str, cap: int = 300) -> list[dict]:
                out = []
                for ln in (text or "").splitlines():
                    s = ln.strip()
                    if len(s) >= 3:
                        out.append({"sec": label, "txt": s[:cap]})
                return out
            card_lines: list[dict] = []
            card_lines.extend(_lines("binding", binding))
            card_lines.extend(_lines("master_table", master_table_txt))
            card_lines.extend(_lines("top3", top3_section_txt))
            card_lines.extend(_lines("bottom", bottom_line))
            # Cap per-card lines to stop pathological archives from
            # bloating the page; 200 lines per card covers full Master
            # Table (11 rows × ~10 fields) + binding + Top-3 + bottom_line
            # with margin.
            if len(card_lines) > 200:
                card_lines = card_lines[:200]

            # Card-level haystack as fallback (legacy/empty cards or
            # short-token matches inside the domain header).
            search_parts: list[str] = [
                domain.lower(),
                binding.lower()[:1500],
                master_table_txt.lower()[:3000],
                top3_section_txt.lower()[:1000],
                bottom_line.lower()[:500],
            ]
            for pick in picks:
                if not isinstance(pick, dict):
                    continue
                search_parts.append((pick.get("ticker") or "").lower())
                search_parts.append((pick.get("company") or "").lower())
                search_parts.append((pick.get("theme") or "").lower())
                search_parts.append((pick.get("thesis_line") or "").lower()[:300])
            for vt in validated_list:
                search_parts.append(vt.lower())
            search_attr = _html.escape(" ".join(p for p in search_parts if p))
            # JSON-encode line index for data attribute. Use json.dumps
            # with ensure_ascii=False then HTML-escape so quotes / angle
            # brackets in master_table content can't break the attribute.
            import json as _json_sc
            lines_attr = _html.escape(_json_sc.dumps(card_lines, ensure_ascii=False))

            # Card itself collapsible — when multiple domains accumulate
            # per day (AI 데이터센터 / 화학 / 기계 / 방산 ...) each one
            # collapses to just the header. Default open ONLY when this is
            # the only card on the date AND it's today; otherwise closed
            # for compactness. NOAH index.html has flat cards (per-ticker
            # cards live inside day groups), but screener cards carry rich
            # multi-section content so individual collapse is needed.
            day_card_count = len(by_date[date])
            card_default_open = (
                date == _today_kst and day_card_count == 1
            )
            card_open_attr = " open" if card_default_open else ""

            # Stable card id used by snippet click handlers to scroll +
            # open the source card. `_filename` is unique within a date,
            # and `_date` disambiguates across days.
            card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
            parts.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{domain}</span>
      <span class="meta">⏱ {ts} · ₩{cost:,.1f} · {elapsed:.0f}s · <span class="ticker-chip" title="{_html.escape(tickers_title)}">✅ {tickers_n}개 ticker</span></span>
      <button class="del-btn" type="button" title="이 screener 기록 삭제">🗑️</button>
    </summary>
    <div class="card-body">
    {analysis_html}
""")
            if picks:
                parts.append('    <table class="picks"><thead><tr>'
                             '<th>#</th><th>Ticker</th><th>Tier</th><th>Company</th>'
                             '<th>1개월</th><th>3개월</th><th>6개월</th><th>α vs sector</th>'
                             '</tr></thead><tbody>')
                for pick in picks:
                    if not isinstance(pick, dict):
                        continue
                    rank = pick.get("rank", "?")
                    ticker = (pick.get("ticker") or "").upper()
                    tier = pick.get("tier", "?")
                    company = _html.escape(pick.get("company", "")[:50])
                    # Look up resolved outcomes
                    out = outcomes.get((r.get("_date", ""), ticker), {}) or {}
                    # Screener pass1 = 1개월(30 캘린더일), pass2 = 3개월
                    # (90)·6개월(180). 변수명은 1m/3m/6m 의미.
                    raw_1m  = out.get("raw")
                    alpha1m = out.get("alpha")
                    extras = {o["days"]: o for o in (out.get("outcomes_extra") or [])}
                    raw_3m  = extras.get(90,  {}).get("raw")
                    raw_6m  = extras.get(180, {}).get("raw")
                    pending_cell = '<span class="pending">⏳</span>'
                    cell_1m = _fmt_pct_signed(raw_1m) if raw_1m else pending_cell
                    cell_3m = _fmt_pct_signed(raw_3m) if raw_3m else "—"
                    cell_6m = _fmt_pct_signed(raw_6m) if raw_6m else "—"
                    cell_alpha = _fmt_pct_signed(alpha1m) if alpha1m else "—"
                    parts.append(
                        f'<tr>'
                        f'<td class="rank">#{rank}</td>'
                        f'<td><code>{_html.escape(ticker)}</code></td>'
                        f'<td><span class="tier-{tier}">{tier}</span></td>'
                        f'<td class="co">{company}</td>'
                        f'<td class="{_outcome_class(raw_1m)}">{cell_1m}</td>'
                        f'<td class="{_outcome_class(raw_3m)}">{cell_3m}</td>'
                        f'<td class="{_outcome_class(raw_6m)}">{cell_6m}</td>'
                        f'<td class="{_outcome_class(alpha1m)}">{cell_alpha}</td>'
                        f'</tr>'
                    )
                parts.append('</tbody></table>')
            # Close card-body + card details
            parts.append('  </div></details>\n')
        # Close the date's details + body wrapper
        parts.append('</div></details>')
    _month_close(parts, _prev_month)

    parts.append("</div>")
    # JS — delete button POSTs to /api/screener_delete (mirror of NOAH
    # /api/delete pattern). On success, fade + remove the card. Server
    # regen rewrites screener.html so a later reload picks up everything.
    parts.append("""
<script>
// Snippet-highlight search (SV dashboard pattern). Typing '변압기' surfaces
// the specific lines mentioning it from binding / master_table / Top-3 /
// bottom_line content, with the match wrapped in <mark>. Clicking a
// snippet jumps to + opens the source card with a transient highlight.
// Empty query restores the default card view.
(function() {
  const inp = document.getElementById('scr-search');
  const clr = document.getElementById('scr-clear');
  const sts = document.getElementById('scr-status');
  const emp = document.getElementById('scr-empty');
  const snp = document.getElementById('scr-snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const dayGroups = Array.from(document.querySelectorAll('details.day'));
  const monthGroups = Array.from(document.querySelectorAll('details.month'));
  if (!inp) return;
  const total = cards.length;

  const SECTION_LABELS = {
    'binding': '📍 binding',
    'master_table': '📊 Master Table',
    'top3': '🏆 Top-3',
    'bottom': '💡 Bottom line',
  };
  const MAX_SNIPPETS = 80;  // soft cap to keep panel rendering fast

  // Pre-parse each card's line index once (JSON.parse is the hot path
  // on every keystroke otherwise). Domain header included as a synthetic
  // 'domain' entry so typing 'AI 데이터센터' returns at least one snippet.
  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const domainEl = c.querySelector('.domain');
    const domainTxt = domainEl ? domainEl.textContent.trim() : '';
    if (domainTxt) lines.unshift({sec: 'domain', txt: domainTxt});
    return {card: c, lines: lines, hay: (c.dataset.search || '')};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function escapeReg(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function highlight(text, q) {
    if (!q) return escapeHtml(text);
    const re = new RegExp(escapeReg(q), 'gi');
    let out = '';
    let last = 0;
    let m;
    const safe = escapeHtml(text);
    // Re-derive match positions against the escaped string (HTML escape
    // preserves byte-for-byte ordering for the chars we search over —
    // CJK / latin / digit — so positions line up).
    while ((m = re.exec(safe)) !== null) {
      out += safe.slice(last, m.index);
      out += '<mark>' + safe.slice(m.index, m.index + m[0].length) + '</mark>';
      last = m.index + m[0].length;
      if (m.index === re.lastIndex) re.lastIndex++;  // safety for empty match
    }
    out += safe.slice(last);
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none';
    snp.innerHTML = '';
    emp.style.display = 'none';
    clearInlineHighlight();
    for (const c of cards) {
      c.style.display = '';
      c.open = (c.dataset.defaultOpen === 'true');
      c.classList.remove('hit-flash');
    }
    for (const d of dayGroups) {
      d.style.display = '';
    }
    for (const m of monthGroups) m.style.display = '';
    sts.textContent = '총 ' + total + '건의 screener 실행';
  }

  function showSnippetsMode(q) {
    // Hide all cards + day groups while in search mode — snippet list
    // becomes the primary view. Card visibility is restored on snippet
    // click (only the clicked card) or on clear.
    for (const c of cards) c.style.display = 'none';
    for (const d of dayGroups) d.style.display = 'none';
    for (const m of monthGroups) m.style.display = 'none';

    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, sec: ln.sec, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }

    if (hits.length === 0) {
      snp.style.display = 'none';
      emp.style.display = 'block';
      sts.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emp.style.display = 'none';

    const cardHitCounts = new Map();
    const parts = [];
    for (const h of hits) {
      const cid = h.card.id;
      cardHitCounts.set(cid, (cardHitCounts.get(cid) || 0) + 1);
      const dateAttr = h.card.dataset.date || '';
      const domainEl = h.card.querySelector('.domain');
      const domainTxt = domainEl ? domainEl.textContent.trim() : '';
      const secLabel = SECTION_LABELS[h.sec] || h.sec;
      parts.push(
        '<div class="snippet" data-target="' + cid +
          '" data-section="' + escapeHtml(h.sec) + '">' +
          '<div class="snippet-meta">' +
            '<span class="snippet-sec">' + escapeHtml(secLabel) + '</span>' +
            '<span class="snippet-card">' + escapeHtml(domainTxt) +
            ' · ' + escapeHtml(dateAttr) + '</span>' +
          '</div>' +
          '<div class="snippet-text">' + highlight(h.txt, q) + '</div>' +
        '</div>'
      );
    }
    snp.innerHTML = parts.join('');
    snp.style.display = 'block';
    const uniq = cardHitCounts.size;
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건 표시)' : '';
    sts.textContent = hits.length + '개 라인 · ' + uniq + '개 카드 매칭' + cap +
                      ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const q = (inp.value || '').trim();
    if (!q) { showCardsMode(); return; }
    showSnippetsMode(q);
  }

  // Walk text nodes inside an element, wrap the first occurrence of q
  // with <mark.snippet-target> so the user can both see + scroll to the
  // exact phrase. Returns the mark element (or null when no match landed).
  // Prior calls' marks are cleared so a second snippet click doesn't
  // accumulate highlights. Text-node walking preserves existing inline
  // structure (e.g. Master Table 의 <b class="mt-section">[전력: 변압기]</b>
  // bolding) — a naive innerHTML replace would wipe that.
  function clearInlineHighlight() {
    document.querySelectorAll('mark.snippet-target').forEach(function(m) {
      const parent = m.parentNode;
      if (!parent) return;
      parent.replaceChild(document.createTextNode(m.textContent), m);
      parent.normalize();
    });
  }
  function highlightInElement(el, q) {
    if (!el || !q) return null;
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: function(node) {
        // Skip text nodes that are already inside a mark (shouldn't
        // happen after clearInlineHighlight, but defensive).
        if (node.parentNode && node.parentNode.tagName === 'MARK') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent;
      const idx = text.toLowerCase().indexOf(ql);
      if (idx >= 0) {
        const parent = node.parentNode;
        const before = text.slice(0, idx);
        const match = text.slice(idx, idx + q.length);
        const after = text.slice(idx + q.length);
        if (before) parent.insertBefore(document.createTextNode(before), node);
        const mk = document.createElement('mark');
        mk.className = 'snippet-target';
        mk.textContent = match;
        parent.insertBefore(mk, node);
        if (after) parent.insertBefore(document.createTextNode(after), node);
        parent.removeChild(node);
        return mk;
      }
    }
    return null;
  }

  // Snippet click → restore source card to view, open it AND its nested
  // details so the matched section is actually rendered, wrap the exact
  // match in <mark.snippet-target>, and scroll that mark into view.
  snp.addEventListener('click', function(ev) {
    const sn = ev.target.closest('.snippet');
    if (!sn) return;
    const tgt = sn.dataset.target;
    if (!tgt) return;
    const card = document.getElementById(tgt);
    if (!card) return;
    clearInlineHighlight();
    // Reveal everything that was hidden by the search filter, then open
    // ONLY the target card + its day group so the user lands somewhere
    // calm rather than every card expanding.
    for (const c of cards) {
      c.style.display = '';
      c.open = false;
      c.classList.remove('hit-flash');
    }
    for (const d of dayGroups) {
      d.style.display = '';
      d.open = false;
    }
    for (const m of monthGroups) { m.style.display = ''; m.open = false; }
    const monthG = card.closest('details.month');
    if (monthG) monthG.open = true;
    const dayG = card.closest('details.day');
    if (dayG) dayG.open = true;
    card.open = true;
    // Open the analysis details (top-level) + master-table nested details
    // when needed, otherwise the data-section element is still display:
    // none inside its closed <details> and scrollIntoView misses.
    const analysisEl = card.querySelector('details.analysis');
    if (analysisEl) analysisEl.open = true;
    const sec = sn.dataset.section || '';
    if (sec === 'master_table') {
      const mtEl = card.querySelector('details.analysis-mt');
      if (mtEl) mtEl.open = true;
    }
    snp.style.display = 'none';

    // Locate the section's analysis-b and wrap the exact match. Domain
    // matches (synthetic 'domain' line) don't have a body element, so
    // fall back to scrolling the card header.
    let scrollTarget = null;
    if (sec && sec !== 'domain') {
      const bodyEl = card.querySelector('.analysis-b[data-section="' + sec + '"]');
      if (bodyEl) {
        const q = (inp.value || '').trim();
        scrollTarget = highlightInElement(bodyEl, q) || bodyEl;
      }
    }
    if (!scrollTarget) {
      scrollTarget = card.querySelector('.card-h') || card;
    }
    card.classList.add('hit-flash');
    setTimeout(function() {
      scrollTarget.scrollIntoView({behavior: 'smooth', block: 'center'});
    }, 50);
    setTimeout(function() {
      card.classList.remove('hit-flash');
    }, 2400);
  });

  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() {
    inp.value = '';
    showCardsMode();
    inp.focus();
  });
})();

document.querySelectorAll('.del-btn').forEach(function(btn) {
  btn.addEventListener('click', function(ev) {
    ev.stopPropagation();
    ev.preventDefault();
    const card = btn.closest('.card');
    if (!card) return;
    const date = card.dataset.date;
    const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📊 ' + date + ' / ' + filename + ' screener 기록을 삭제할까요?')) return;
    btn.disabled = true;
    btn.textContent = '⏳';
    fetch('api/screener_delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: filename})
    }).then(function(r) {
      return r.json().then(function(d) { return {status: r.status, body: d}; });
    }).then(function(res) {
      if (res.status === 200 && res.body && res.body.ok) {
        card.style.transition = 'opacity 0.2s';
        card.style.opacity = '0';
        setTimeout(function() { card.remove(); }, 200);
      } else {
        alert('삭제 실패: ' + (res.body && res.body.error || res.status));
        btn.disabled = false;
        btn.textContent = '🗑️';
      }
    }).catch(function(err) {
      alert('삭제 실패: ' + err);
      btn.disabled = false;
      btn.textContent = '🗑️';
    });
  });
});
</script>
</body></html>
""")
    return "".join(parts)


_SCREENER_CSS = (
    """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bottleneck Screener — Archive</title>
<script>""" + _THEME_JS + """</script>
<style>
/* Time-based light/dark theme (Asia/Seoul) — _THEME_JS toggles
   `data-theme="dark"` on the <html> element between 19:00-07:00.
   Mirrors the NOAH index/detail pages. Variables below adapt: light
   defaults at `:root`, dark overrides at `:root[data-theme="dark"]`. */
:root {
  --bg:#f8fafc; --card:#ffffff; --border:#e5e7eb;
  --text:#1f2937; --muted:#6b7280; --accent:#0ea5e9;
  --pos:#059669; --neg:#dc2626; --neu:#6b7280; --pending:#d97706;
  --accent-soft:rgba(14,165,233,0.07);
  --accent-soft2:rgba(14,165,233,0.14);
  --surface-tint:rgba(0,0,0,0.05);
  --surface-tint-strong:rgba(0,0,0,0.07);
  --row-border:rgba(0,0,0,0.05);
  --tier-l-bg:rgba(14,165,233,0.12); --tier-l-fg:#0369a1;
  --tier-m-bg:rgba(16,185,129,0.14); --tier-m-fg:#047857;
  --tier-s-bg:rgba(245,158,11,0.18); --tier-s-fg:#b45309;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.45); --mark-fg:#7c2d12;
  --mark-target-bg:rgba(245,158,11,0.7); --mark-target-fg:#1f2937;
}
:root[data-theme="dark"] {
  --bg:#0F1219; --card:#1A1F2B; --border:#2A3142;
  --text:#E8ECF4; --muted:#94A3B8; --accent:#3B82F6;
  --pos:#10B981; --neg:#EF4444; --neu:#6B7280; --pending:#F59E0B;
  --accent-soft:rgba(59,130,246,0.06);
  --accent-soft2:rgba(59,130,246,0.15);
  --surface-tint:rgba(255,255,255,0.04);
  --surface-tint-strong:rgba(255,255,255,0.06);
  --row-border:rgba(255,255,255,0.04);
  --tier-l-bg:rgba(59,130,246,0.15); --tier-l-fg:#93C5FD;
  --tier-m-bg:rgba(16,185,129,0.15); --tier-m-fg:#6EE7B7;
  --tier-s-bg:rgba(245,158,11,0.15); --tier-s-fg:#FCD34D;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.35); --mark-fg:#FCD34D;
  --mark-target-bg:rgba(245,158,11,0.55); --mark-target-fg:#fff;
}
* { box-sizing: border-box; }
body { background:var(--bg); color:var(--text); margin:0;
  font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',
    'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji','Twemoji Mozilla',sans-serif;
  line-height:1.55; -webkit-font-smoothing:antialiased; }
.wrap { max-width:1100px; margin:0 auto; padding:24px 16px 64px; }
.nav { margin-bottom:12px; }
.nav a { color:var(--accent); text-decoration:none; font-size:13px; }
.nav a:hover { text-decoration:underline; }
h1 { font-size:22px; margin:0 0 4px; }
h2.date { font-size:14px; color:var(--muted); margin:28px 0 12px;
  padding-bottom:6px; border-bottom:1px solid var(--border); }
details.month { margin:28px 0 0; }
details.month summary.month-head { cursor:pointer; font-size:16px;
  font-weight:700; padding:12px 4px; border-bottom:2px solid var(--accent-soft);
  display:flex; align-items:center; justify-content:space-between;
  list-style:none; color:var(--text); user-select:none; }
details.month summary.month-head::-webkit-details-marker { display:none; }
details.month summary.month-head::before { content:"▸"; color:var(--accent);
  margin-right:8px; transition:transform 0.15s; }
details.month[open] summary.month-head::before { content:"▾"; }
details.month summary.month-head:hover { background:var(--accent-soft); }
details.month .count { color:var(--muted); font-size:12px; font-weight:normal; }
details.month .month-body { padding-top:4px; padding-left:6px; }
details.day { margin:24px 0 0; }
details.day summary.day-head { cursor:pointer; font-size:15px;
  font-weight:600; padding:10px 4px; border-bottom:1px solid var(--border);
  display:flex; align-items:center; justify-content:space-between;
  list-style:none; color:var(--text); user-select:none; }
details.day summary.day-head::-webkit-details-marker { display:none; }
details.day summary.day-head::before { content:"▸"; color:var(--accent);
  margin-right:8px; transition:transform 0.15s; }
details.day[open] summary.day-head::before { content:"▾"; }
details.day summary.day-head:hover { background:var(--accent-soft); }
details.day .count { color:var(--muted); font-size:12px; font-weight:normal; }
details.day .day-body { padding-top:14px; }
.sub { color:var(--muted); font-size:13px; margin:0 0 24px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px; margin-bottom:24px; }
.stat { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:14px 16px; }
.stat-v { font-size:20px; font-weight:600; }
.stat-l { color:var(--muted); font-size:11px; margin-top:2px;
  text-transform:uppercase; letter-spacing:0.5px; }
.card, details.card { background:var(--card); border:1px solid var(--border);
  border-radius:12px; padding:16px 18px; margin-bottom:14px; }
details.card { padding:0; }
details.card summary.card-h { cursor:pointer; list-style:none;
  padding:16px 18px; user-select:none; border-radius:12px; }
details.card[open] summary.card-h {
  border-bottom:1px solid var(--border); border-radius:12px 12px 0 0; }
details.card summary.card-h::-webkit-details-marker { display:none; }
details.card .card-toggle { color:var(--accent); font-weight:600;
  margin-right:2px; transition:transform 0.15s; }
details.card[open] .card-toggle { transform:rotate(90deg); display:inline-block; }
details.card .card-body { padding:14px 18px 18px; }
.card-h { display:flex; justify-content:space-between; align-items:center;
  gap:12px; flex-wrap:wrap; margin-bottom:12px; }
.domain { font-weight:600; font-size:15px; flex:1; min-width:200px; }
.meta { color:var(--muted); font-size:13px; font-family:'IBM Plex Mono',monospace; }
.del-btn { background:none; border:none; cursor:pointer;
  color:var(--muted); font-size:16px; padding:4px 8px; border-radius:6px;
  margin-left:auto; }
.del-btn:hover { color:var(--neg); background:rgba(239,68,68,0.10); }
.del-btn:disabled { opacity:0.5; cursor:wait; }
.search-bar { display:flex; gap:8px; margin-bottom:14px; }
.search-bar input { flex:1; background:var(--card);
  border:1px solid var(--border); border-radius:8px; padding:10px 14px;
  color:var(--text); font-size:14px; font-family:inherit; outline:none; }
.search-bar input:focus { border-color:var(--accent); }
.search-bar button { background:var(--search-btn-bg); color:white; border:none;
  border-radius:8px; padding:0 18px; font-size:13px; font-weight:600;
  cursor:pointer; transition:transform 0.05s, background 0.1s; }
.search-bar button:hover { background:var(--search-btn-hover); }
.search-bar button:active { transform:scale(0.97); }
.status-line { color:var(--muted); font-size:12px; margin:0 0 12px; }
table.picks { width:100%; border-collapse:collapse; font-size:15px; }
table.picks th { text-align:left; color:var(--muted); font-weight:500;
  padding:8px 6px; border-bottom:1px solid var(--border); font-size:12px;
  text-transform:uppercase; letter-spacing:0.3px; }
table.picks td { padding:8px 6px; border-bottom:1px solid var(--row-border); }
table.picks tr:last-child td { border-bottom:none; }
td.rank { font-weight:600; color:var(--accent); width:36px; }
td.co { color:var(--muted); }
td code { background:var(--surface-tint); padding:2px 6px;
  border-radius:4px; font-size:12px; }
td .tier-L { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-M { background:var(--tier-m-bg); color:var(--tier-m-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td .tier-S { background:var(--tier-s-bg); color:var(--tier-s-fg);
  padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
td.pos { color:var(--pos); font-weight:600; }
td.neg { color:var(--neg); font-weight:600; }
td.neu { color:var(--muted); }
.pending { color:var(--pending); }
.empty { background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:30px; text-align:center; color:var(--muted);
  font-size:14px; }
.empty code { background:var(--surface-tint-strong); padding:2px 8px;
  border-radius:4px; }
details.analysis { margin:10px 0 14px; }
details.analysis summary { cursor:pointer; color:var(--accent);
  font-size:15px; padding:7px 11px; background:var(--accent-soft);
  border-radius:6px; user-select:none; list-style:none; }
details.analysis summary::-webkit-details-marker { display:none; }
details.analysis summary::before { content:"▸ "; margin-right:4px; }
details.analysis[open] summary::before { content:"▾ "; }
details.analysis summary:hover { background:var(--accent-soft2); }
.analysis-sec { margin:12px 4px 0; padding:10px 12px;
  background:var(--surface-tint); border-left:2px solid var(--border);
  border-radius:0 6px 6px 0; }
.analysis-h { color:var(--muted); font-size:13px; font-weight:600;
  margin-bottom:6px; text-transform:none; letter-spacing:0; }
/* 카드 본문 (Screener Top-3 근거·binding·bottom_line + Daily Byte 브리프).
   13px 는 모바일에서 작음 → 15px + line-height 1.65 (SV 본문 수준).
   2026-05-29 사용자 요청. */
.analysis-b { color:var(--text); font-size:14px; line-height:1.65;
  white-space:pre-wrap; }
.ticker-chip { cursor:help; border-bottom:1px dotted var(--muted); }
.ticker-chip:hover { color:var(--accent); }
details.analysis-mt { margin:12px 4px 0; }
details.analysis-mt summary { cursor:pointer; color:var(--accent);
  font-size:14px; padding:6px 10px; background:var(--accent-soft2);
  border-radius:6px; list-style:none; user-select:none; }
details.analysis-mt summary::-webkit-details-marker { display:none; }
details.analysis-mt summary::before { content:"▸ "; margin-right:4px; }
details.analysis-mt[open] summary::before { content:"▾ "; }
details.analysis-mt summary:hover { background:var(--accent-soft2); }
details.analysis-mt .analysis-sec { margin-top:8px; }
.mt-section { color:var(--accent); font-weight:700; font-size:13.5px;
  display:inline-block; padding:2px 0; margin-top:4px; }
/* Snippet-highlight search panel (SV dashboard pattern). Shown only
   when a query is active; clicking a snippet jumps to its source card. */
.snippets { display:flex; flex-direction:column; gap:8px; margin:12px 0 24px; }
.snippet { background:var(--card); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:10px 14px; cursor:pointer; transition:background 0.1s,
  border-color 0.1s; }
.snippet:hover { background:var(--accent-soft); border-left-color:var(--accent); }
.snippet-meta { display:flex; gap:10px; font-size:11px; color:var(--muted);
  margin-bottom:4px; align-items:center; }
.snippet-sec { background:var(--tier-l-bg); color:var(--tier-l-fg);
  padding:2px 8px; border-radius:4px; font-weight:600; }
.snippet-card { font-family:'IBM Plex Mono',monospace; }
.snippet-text { color:var(--text); font-size:13px; line-height:1.55;
  white-space:pre-wrap; word-break:break-word; }
mark { background:var(--mark-bg); color:var(--mark-fg); padding:1px 3px;
  border-radius:3px; font-weight:600; }
/* In-body highlight when a snippet is clicked — stronger than the
   snippet-panel mark so the user's eye finds the match after the
   smooth-scroll lands. Pulses briefly then settles. */
mark.snippet-target { background:var(--mark-target-bg); color:var(--mark-target-fg);
  box-shadow:0 0 0 2px rgba(245,158,11,0.4); animation:markPulse 1.8s ease-out; }
@keyframes markPulse {
  0%   { background:rgba(245,158,11,0.95); box-shadow:0 0 0 4px rgba(245,158,11,0.6); }
  100% { background:var(--mark-target-bg); box-shadow:0 0 0 2px rgba(245,158,11,0.4); }
}
/* Brief pulse when a snippet click scrolls to a card — fades after 2s
   so the user immediately sees which card the match came from. */
@keyframes hitFlash {
  0%   { box-shadow:0 0 0 2px rgba(245,158,11,0.7); }
  60%  { box-shadow:0 0 0 2px rgba(245,158,11,0.35); }
  100% { box-shadow:0 0 0 0 rgba(245,158,11,0); }
}
details.card.hit-flash { animation:hitFlash 2.4s ease-out; }
</style></head><body>
"""
)


def _render_screener_domains_page() -> str:
    """Generate a self-contained HTML page listing all registered screener
    domains (auto-discovered from bot.screener_themes). Renders the same
    info as the /screener_list Telegram command but as a browseable page
    so users can click and see the full taxonomy alongside the run
    archive. Per CLAUDE.md 'Screener 도메인 목록은 _HELP_TEXT inline
    금지' rule (2026-05-29), this page is the canonical user-facing
    surface for the domain catalog. Future Wave 2-B / Wave 3 / Wave ∞
    domain additions = module drop only — this page + /screener_list
    auto-update; _HELP_TEXT never grows.

    2026-05-29 follow-up: append a '📜 변경 이력' section so the user
    can see when each Wave shipped + what was added/removed at each
    step. Sourced from `bot.screener_history.load_history()`.
    """
    import html as _html
    from collections import defaultdict
    from bot.screener_themes import list_domains
    from bot.screener_history import load_history
    ds = list_domains()

    # Group by layer (L1_TREND / L2_SECTOR / L3_INDUSTRY) per user request
    # 2026-05-29: dashboard reorganized by 3-layer model; previous full
    # change-history section collapsed into a single footer line ("최근
    # 추가 N개" — actionable summary instead of chronological log).
    _LAYER_META = [
        ("L1_TREND",    "📈 L1 Trend",   "Cross-cutting cycle 베팅 — 공식 sector 분류 외"),
        ("L2_SECTOR",   "🏢 L2 Sector",  "11 공식 sector (미국 GICS-like)"),
        ("L3_INDUSTRY", "🔬 L3 Industry","각 L2 아래 sub-industry"),
        # AD_HOC = `/screener <자유어>` 5회+ 사용 후 자동 promoted 정식 모듈
        # (bot/screener_freetext.promote_to_module). 사용자 수동 reclassify
        # 전까지 별도 layer 로 노출.
        ("AD_HOC",      "🆕 자유어 promoted", "자유어 5회+ 사용 → 정식 모듈 자동 생성 (수동 reclassify 대기)"),
    ]
    by_layer: dict[str, list[dict]] = defaultdict(list)
    for d in ds:
        by_layer[d.get("layer") or "L1_TREND"].append(d)

    def _render_card(d: dict) -> str:
        slug = d["slug"]
        domain = _html.escape(d["domain"])
        aliases = [a for a in d["aliases"] if a.lower() != slug]
        alias_html = ""
        if aliases:
            chips = "".join(
                f'<span class="alias">{_html.escape(a)}</span>' for a in aliases
            )
            alias_html = f'<div class="aliases">{chips}</div>'
        # Per-slug shortcut commands (`/screener_<slug>`) — single tap
        # fires the right domain run.
        return (
            f'<div class="dom-card">'
            f'<div class="dom-head">'
            f'<code class="slug">/screener_{_html.escape(slug)}</code>'
            f'<span class="dom-name">{domain}</span>'
            f'</div>'
            f'{alias_html}'
            f'</div>'
        )

    sections: list[str] = []
    _layer_css_class = {
        "L1_TREND": "l1",
        "L2_SECTOR": "l2",
        "L3_INDUSTRY": "l3",
        "AD_HOC": "ad_hoc",
    }
    # Per-layer collapsible — 전부 default 접힘 (사용자 정책 2026-06-01
    # "애도 똑같이 접혀있게" — index/Daily Byte/screener/realestate/cheongyak
    # 의 orphan/과거 그룹 접힘 정책과 일관). 사용자가 헤더 클릭으로 펼침.
    # 검색 시 매칭 layer 만 자동 펼침 (showFilter 로직 유지).
    _layer_default_open = {
        "L1_TREND": False,
        "L2_SECTOR": False,
        "L3_INDUSTRY": False,
        "AD_HOC": False,
    }
    for layer_key, layer_label, layer_desc in _LAYER_META:
        cards = by_layer.get(layer_key, [])
        if not cards:
            continue
        cards_html = "\n".join(_render_card(d) for d in cards)
        css_class = _layer_css_class.get(layer_key, "")
        open_attr = " open" if _layer_default_open.get(layer_key, False) else ""
        sections.append(
            f'<details class="layer-details {css_class}"{open_attr}>'
            f'<summary class="layer-section {css_class}">'
            f'<h2 class="layer-h">{layer_label} '
            f'<span class="layer-count">({len(cards)}개)</span></h2>'
            f'<p class="layer-desc">{_html.escape(layer_desc)}</p>'
            f'</summary>'
            f'<div class="layer-body">{cards_html}</div>'
            f'</details>'
        )
    body = "\n".join(sections) if sections else (
        '<div class="empty">아직 등록된 도메인이 없습니다.</div>'
    )
    # Filter input — instant client-side filtering across all layers.
    # Forces every <details> open while filter is active so matches in
    # collapsed sections still surface. Clearing the input restores the
    # default open/closed state per layer.
    filter_html = (
        '<div class="filter-bar">'
        '<input id="dom-filter" type="text" '
        'placeholder="🔍 도메인/별칭 검색 (예: 반도체, banks, 휴머노이드)" '
        'autocomplete="off" spellcheck="false">'
        '<button id="dom-filter-clear" type="button" title="초기화">×</button>'
        '</div>'
    )

    # 변경 이력 footer — single line summary (사용자 요청 2026-05-29
    # "변경이력으로 남기지 말고 그냥 3-layer 기준으로 나눠져 ... 추가,
    # 변경, 제거가 있으면 그걸 반영"). Full chronological log replaced
    # by a tight summary of the most recent change: latest ts + added
    # slugs. Initial seed entry suppressed from the footer.
    history = load_history()
    history_html = ""
    if history:
        recent_change = next(
            (e for e in history if not e.get("initial")),
            None,
        )
        if recent_change:
            ts = _html.escape(
                str(recent_change.get("ts", ""))[:16].replace("T", " ")
            )
            added = recent_change.get("added") or []
            removed = recent_change.get("removed") or []
            parts: list[str] = []
            if added:
                parts.append(
                    "추가 "
                    + ", ".join(
                        f'<code>/screener_{_html.escape(s)}</code>' for s in added[:5]
                    )
                    + (f" 외 {len(added) - 5}개" if len(added) > 5 else "")
                )
            if removed:
                parts.append(
                    "제거 "
                    + ", ".join(
                        f'<code>{_html.escape(s)}</code>' for s in removed[:5]
                    )
                    + (f" 외 {len(removed) - 5}개" if len(removed) > 5 else "")
                )
            history_html = (
                '<p class="sub history-footer" style="margin-top:24px;'
                'padding-top:12px;border-top:1px solid var(--border)">'
                f'📜 최근 변경 ({ts} KST) — '
                + " · ".join(parts) +
                f' → 총 {recent_change.get("total_after", 0)}개</p>'
            )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 Screener 도메인 목록</title>
<script>{_THEME_JS}</script>
<style>{_INDEX_CSS}
.dom-card {{ background:var(--card); border:1px solid var(--border);
  border-left:3px solid var(--accent); border-radius:8px;
  padding:12px 16px; margin-bottom:10px;
  transition:border-left-width 0.1s, box-shadow 0.1s; }}
.dom-card:hover {{ border-left-width:5px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06); }}
:root[data-theme="dark"] .dom-card:hover {{
  box-shadow:0 1px 4px rgba(0,0,0,0.3); }}
.dom-head {{ display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
.slug {{ background:rgba(14,165,233,0.15); color:var(--accent);
  padding:4px 11px; border-radius:6px; font-size:13px; font-weight:700;
  font-family:'IBM Plex Mono',monospace;
  border:1px solid rgba(14,165,233,0.25); }}
:root[data-theme="dark"] .slug {{
  background:rgba(59,130,246,0.18);
  border-color:rgba(59,130,246,0.3); }}
.dom-name {{ font-size:16px; color:var(--fg); flex:1; min-width:200px;
  font-weight:500; }}
.aliases {{ margin-top:10px; display:flex; gap:6px; flex-wrap:wrap; }}
/* Alias chips — explicit colors per theme so contrast holds on both
   light (washed-out gray on white) and dark (subtle gray on charcoal).
   2026-05-29 사용자 가독성 fix: 라이트 모드에서 alias 가 거의 안 보
   여서 chip 배경 #f1f5f9 (slate-100) + 본문 색 #475569 (slate-600)
   으로 명도 대비 확보. */
.alias {{ background:#e2e8f0; color:#475569;
  padding:3px 9px; border-radius:4px; font-size:11px;
  font-family:'IBM Plex Mono',monospace; font-weight:500; }}
:root[data-theme="dark"] .alias {{ background:rgba(255,255,255,0.07);
  color:#cbd5e1; }}
.hist-entry {{ background:var(--card); border:1px solid var(--border);
  border-radius:8px; padding:10px 14px; margin-bottom:8px; }}
.hist-head {{ display:flex; gap:12px; align-items:center;
  flex-wrap:wrap; font-size:13px; color:var(--fg-soft); margin-bottom:6px; }}
.hist-label {{ font-weight:600; color:var(--fg); }}
.hist-ts {{ font-family:'IBM Plex Mono',monospace; }}
.hist-total {{ margin-left:auto; color:var(--accent); font-weight:600; }}
.hist-row {{ font-size:14px; margin:4px 0; line-height:1.7; }}
.hist-row b {{ color:var(--fg-soft); font-weight:500;
  margin-right:6px; font-size:11px; }}
.hist-add, .hist-rem {{ display:inline-block; margin:2px 4px 2px 0; }}
.hist-add code {{ background:rgba(16,185,129,0.12); color:#10B981;
  padding:2px 8px; border-radius:4px; font-size:11px; }}
.hist-rem code {{ background:rgba(239,68,68,0.12); color:#EF4444;
  padding:2px 8px; border-radius:4px; font-size:11px;
  text-decoration:line-through; }}
/* 3-layer grouping (L1 / L2 / L3) — section headers between domain
   card stacks. Each layer has its own accent strip on the left so the
   user can visually distinguish L1 (sky) / L2 (emerald) / L3 (purple)
   as they scroll. 2026-05-29 가독성 fix. */
.layer-section {{ margin:36px 0 16px;
  padding:18px 0 6px 16px;
  border-top:1px solid var(--border);
  border-left:3px solid var(--accent);
  background:linear-gradient(90deg,
    rgba(14,165,233,0.05) 0%,
    transparent 240px); }}
.layer-section:first-of-type {{ margin-top:24px; border-top:none; padding-top:8px; }}
.layer-section.l1 {{ border-left-color:#0ea5e9; }}
.layer-section.l1 {{ background:linear-gradient(90deg,
  rgba(14,165,233,0.07) 0%, transparent 280px); }}
.layer-section.l2 {{ border-left-color:#10b981; }}
.layer-section.l2 {{ background:linear-gradient(90deg,
  rgba(16,185,129,0.07) 0%, transparent 280px); }}
.layer-section.l3 {{ border-left-color:#8b5cf6; }}
.layer-section.l3 {{ background:linear-gradient(90deg,
  rgba(139,92,246,0.07) 0%, transparent 280px); }}
.layer-section.ad_hoc {{ border-left-color:#f59e0b; }}
.layer-section.ad_hoc {{ background:linear-gradient(90deg,
  rgba(245,158,11,0.08) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l1 {{
  background:linear-gradient(90deg, rgba(59,130,246,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l2 {{
  background:linear-gradient(90deg, rgba(16,185,129,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l3 {{
  background:linear-gradient(90deg, rgba(167,139,250,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.ad_hoc {{
  background:linear-gradient(90deg, rgba(251,191,36,0.12) 0%, transparent 280px); }}
.layer-h {{ font-size:20px; margin:0 0 4px;
  color:var(--fg); font-weight:700; }}
.layer-count {{ color:var(--fg-soft); font-size:14px; font-weight:500;
  margin-left:8px; }}
.layer-desc {{ margin:0 0 14px; color:var(--fg-soft); font-size:13px; }}
.history-footer code {{ background:rgba(16,185,129,0.12);
  color:#059669; padding:1px 6px; border-radius:4px;
  font-size:11px; font-weight:600; }}
:root[data-theme="dark"] .history-footer code {{
  background:rgba(16,185,129,0.18); color:#34d399; }}
/* Collapsible layer sections — L1 default open, L2/L3 closed so
   initial paint stays light on mobile (48 L3 cards 한꺼번에 펼치니
   느리다는 사용자 보고 2026-05-29). 헤더 탭 시 펼침. */
details.layer-details {{ margin:0; }}
details.layer-details summary {{ cursor:pointer; list-style:none;
  user-select:none; }}
details.layer-details summary::-webkit-details-marker {{ display:none; }}
details.layer-details summary::after {{ content:"▾";
  position:absolute; right:18px; top:18px;
  color:var(--fg-soft); font-size:14px;
  transition:transform 0.15s; }}
details.layer-details:not([open]) summary::after {{
  content:"▸"; }}
details.layer-details summary {{ position:relative; }}
.layer-body {{ padding-top:6px; }}
/* Quick filter — instant client-side search across all layer cards.
   Forces every <details open> while typing so matches in collapsed
   sections still surface. */
.filter-bar {{ display:flex; gap:8px; margin:20px 0 16px;
  position:sticky; top:0; z-index:10;
  background:var(--bg); padding:12px 0;
  border-bottom:1px solid var(--border); }}
.filter-bar input {{ flex:1; padding:10px 14px; font-size:14px;
  color:var(--fg); background:var(--card);
  border:1px solid var(--border); border-radius:8px;
  outline:none; transition:border-color 0.12s; }}
.filter-bar input:focus {{ border-color:var(--accent); }}
.filter-bar button {{ padding:10px 16px; font-size:14px;
  background:var(--card); color:var(--fg-soft);
  border:1px solid var(--border); border-radius:8px;
  cursor:pointer; transition:color 0.1s, border-color 0.1s; }}
.filter-bar button:hover {{ color:var(--fg);
  border-color:var(--accent); }}
.dom-card.hidden {{ display:none; }}
.layer-details.hidden {{ display:none; }}
</style>
</head>
<body>
<div class="wrap">
  <p class="sub">
    <a href="index.html">← NOAH 종목 분석</a> ·
    <a href="screener.html">📊 Bottleneck Screener Archive</a> ·
    <a href="gics_candidates.html">🧬 분기 GICS / 신규 산업 후보</a>
  </p>
  <h1>📊 Screener 도메인 목록 <span style="color:var(--fg-soft);font-size:16px;font-weight:400">({len(ds)}개 · auto-discovered)</span></h1>
  <p class="sub">텔레그램: <code>/screener_&lt;슬러그&gt;</code> 클릭 한 번으로 즉시 실행 · 별칭은 <code>/screener &lt;별칭&gt;</code> 으로 지원
  · 동일 목록 텔레그램 = <code>/screener_list</code>.</p>
  <p class="sub"><b>3-layer 도메인 모델</b> — L1 Trend (cross-cutting cycle 베팅) · L2 Sector (미국 GICS-like 정식 분류) · L3 Industry (각 L2 sector 의 sub-industry).
  새 도메인 추가는 <code>bot/screener_themes/&lt;slug&gt;.py</code> 모듈 1 개 drop 만으로 본 페이지에 자동 반영.
  분기마다(3·6·9·12월 5일) Pro+web search 가 신규 산업 후보를 식별 → <a href="gics_candidates.html">🧬 GICS 후보 페이지</a> 에 누적, 사용자 검증 후 모듈 add 결정.</p>
  {filter_html}
  {body}
  {history_html}
</div>
<script>
(function() {{
  var inp = document.getElementById('dom-filter');
  var clr = document.getElementById('dom-filter-clear');
  if (!inp) return;
  var cards = Array.from(document.querySelectorAll('.dom-card'));
  var details = Array.from(document.querySelectorAll('details.layer-details'));
  // Cache the original default-open state so clearing the filter
  // restores L1 open / L2 L3 closed (instead of leaving them all open).
  var defaultOpen = details.map(function(d) {{ return d.hasAttribute('open'); }});
  // Pre-extract searchable text per card so each keystroke is O(N).
  var cardText = cards.map(function(c) {{
    return (c.textContent || '').toLowerCase().replace(/\\s+/g, ' ');
  }});
  function applyFilter() {{
    var q = (inp.value || '').trim().toLowerCase();
    if (!q) {{
      cards.forEach(function(c) {{ c.classList.remove('hidden'); }});
      details.forEach(function(d, i) {{
        d.classList.remove('hidden');
        if (defaultOpen[i]) d.setAttribute('open', '');
        else d.removeAttribute('open');
      }});
      return;
    }}
    cards.forEach(function(c, i) {{
      var match = cardText[i].indexOf(q) >= 0;
      c.classList.toggle('hidden', !match);
    }});
    // Open every <details> with at least one visible card; hide ones
    // that have no match at all so the user can scan results faster.
    details.forEach(function(d) {{
      var any = d.querySelectorAll('.dom-card:not(.hidden)').length > 0;
      d.classList.toggle('hidden', !any);
      if (any) d.setAttribute('open', '');
    }});
  }}
  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() {{
    inp.value = '';
    applyFilter();
    inp.focus();
  }});
}})();
</script>
</body>
</html>
"""


def regenerate_screener_index() -> None:
    """Scan screener archive + memory, write screener.html + screener_
    domains.html under ARCHIVE_ROOT. Called from screener.py after a
    successful run AND from auto_resolve.py after the 5/15/30d outcome
    pass. All errors swallowed."""
    try:
        runs = _load_screener_runs()
        outcomes = _load_screener_outcomes()
        html = _render_screener_page(runs, outcomes)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "screener.html").write_text(html, encoding="utf-8")
        log.info("dashboard: screener.html regenerated (%d runs, %d outcomes)",
                 len(runs), len(outcomes))
    except Exception as exc:
        log.warning("dashboard: screener regen failed: %s", exc)
    # Domain registry page — separate try so a render error here doesn't
    # block the main screener archive write above. Records a history
    # snapshot BEFORE rendering so any new domains land in the page's
    # '📜 변경 이력' section on the same regen pass.
    try:
        from bot.screener_history import record_snapshot
        record_snapshot()
    except Exception as exc:
        log.warning("dashboard: screener_history record failed: %s", exc)
    try:
        domains_html = _render_screener_domains_page()
        (ARCHIVE_ROOT / "screener_domains.html").write_text(
            domains_html, encoding="utf-8"
        )
        log.info("dashboard: screener_domains.html regenerated")
    except Exception as exc:
        log.warning("dashboard: screener_domains regen failed: %s", exc)


# ── Daily Byte archive view ──────────────────────────────────────────────
# 장 마감 후 KR 수급 브리프 (bot/daily_kr_flow.py). Daily(한국거래일 19:00,
# 주말·공휴일 skip) + Weekly(일 22:00, SV weekly 와 동일 시각) run 을 date-그룹
# 카드로 렌더. screener.html 의 theme(_SCREENER_CSS)·검색창·🗑️ 휴지통
# UX 를 그대로 mirror — 차이는 카드가 단일 브리프 본문(섹션 분리 없음)
# 이라는 점 + Daily/Weekly kind 뱃지.

_DAILY_BYTE_JS = """
<script>
// Snippet-highlight search + 🗑️ delete (mirrors Bottleneck Screener UX).
// Reuses scr-* element ids + .card/.day classes from _SCREENER_CSS. Daily
// Byte cards carry a single 'brief' section, so the search indexes brief
// lines; clicking a snippet opens + scrolls to the source card.
(function() {
  const inp = document.getElementById('scr-search');
  const clr = document.getElementById('scr-clear');
  const sts = document.getElementById('scr-status');
  const emp = document.getElementById('scr-empty');
  const snp = document.getElementById('scr-snippets');
  const cards = Array.from(document.querySelectorAll('.card'));
  const dayGroups = Array.from(document.querySelectorAll('details.day'));
  const monthGroups = Array.from(document.querySelectorAll('details.month'));
  if (!inp) return;
  const total = cards.length;
  const MAX_SNIPPETS = 80;

  const cardData = cards.map(function(c) {
    let lines = [];
    try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
    const t = c.querySelector('.domain');
    const titleTxt = t ? t.textContent.trim() : '';
    if (titleTxt) lines.unshift({sec: 'title', txt: titleTxt});
    return {card: c, lines: lines};
  });

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function(ch) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];
    });
  }
  function highlight(text, q) {
    const safe = escapeHtml(text);
    if (!q) return safe;
    const lt = text.toLowerCase(), lq = q.toLowerCase();
    let out = '', last = 0, idx;
    while ((idx = lt.indexOf(lq, last)) >= 0) {
      out += escapeHtml(text.slice(last, idx));
      out += '<mark>' + escapeHtml(text.slice(idx, idx + q.length)) + '</mark>';
      last = idx + q.length;
    }
    out += escapeHtml(text.slice(last));
    return out;
  }

  function showCardsMode() {
    snp.style.display = 'none'; snp.innerHTML = ''; emp.style.display = 'none';
    for (const c of cards) { c.style.display = ''; c.open = (c.dataset.defaultOpen === 'true'); c.classList.remove('hit-flash'); }
    for (const d of dayGroups) d.style.display = '';
    for (const m of monthGroups) m.style.display = '';
    sts.textContent = '총 ' + total + '건의 Daily Byte 브리프';
  }

  function showSnippetsMode(q) {
    for (const c of cards) c.style.display = 'none';
    for (const d of dayGroups) d.style.display = 'none';
    for (const m of monthGroups) m.style.display = 'none';
    const ql = q.toLowerCase();
    const hits = [];
    for (const cd of cardData) {
      for (const ln of cd.lines) {
        if ((ln.txt || '').toLowerCase().indexOf(ql) >= 0) {
          hits.push({card: cd.card, txt: ln.txt});
          if (hits.length >= MAX_SNIPPETS) break;
        }
      }
      if (hits.length >= MAX_SNIPPETS) break;
    }
    if (hits.length === 0) {
      snp.style.display = 'none'; emp.style.display = 'block';
      sts.textContent = '0건 매칭 (검색: "' + q + '")';
      return;
    }
    emp.style.display = 'none';
    const counts = new Map(); const parts = [];
    for (const h of hits) {
      const cid = h.card.id; counts.set(cid, (counts.get(cid) || 0) + 1);
      const dateAttr = h.card.dataset.date || '';
      const tEl = h.card.querySelector('.domain');
      const tTxt = tEl ? tEl.textContent.trim() : '';
      parts.push('<div class="snippet" data-target="' + cid + '">' +
        '<div class="snippet-meta"><span class="snippet-card">' +
        escapeHtml(tTxt) + ' · ' + escapeHtml(dateAttr) + '</span></div>' +
        '<div class="snippet-text">' + highlight(h.txt, q) + '</div></div>');
    }
    snp.innerHTML = parts.join(''); snp.style.display = 'block';
    const cap = hits.length >= MAX_SNIPPETS ? ' (상위 ' + MAX_SNIPPETS + '건)' : '';
    sts.textContent = hits.length + '개 라인 · ' + counts.size + '개 카드 매칭' + cap + ' (검색: "' + q + '")';
  }

  function applyFilter() {
    const q = (inp.value || '').trim();
    if (!q) { showCardsMode(); return; }
    showSnippetsMode(q);
  }

  snp.addEventListener('click', function(ev) {
    const sn = ev.target.closest('.snippet'); if (!sn) return;
    const tgt = sn.dataset.target; const card = document.getElementById(tgt); if (!card) return;
    for (const c of cards) { c.style.display = ''; c.open = false; c.classList.remove('hit-flash'); }
    for (const d of dayGroups) { d.style.display = ''; d.open = false; }
    for (const m of monthGroups) { m.style.display = ''; m.open = false; }
    const monthG = card.closest('details.month'); if (monthG) monthG.open = true;
    const dayG = card.closest('details.day'); if (dayG) dayG.open = true;
    card.open = true; snp.style.display = 'none';
    card.classList.add('hit-flash');
    const tgtEl = card.querySelector('.card-h') || card;
    setTimeout(function() { tgtEl.scrollIntoView({behavior: 'smooth', block: 'center'}); }, 50);
    setTimeout(function() { card.classList.remove('hit-flash'); }, 2400);
  });

  inp.addEventListener('input', applyFilter);
  clr.addEventListener('click', function() { inp.value = ''; showCardsMode(); inp.focus(); });
})();

document.querySelectorAll('.del-btn').forEach(function(btn) {
  btn.addEventListener('click', function(ev) {
    ev.stopPropagation(); ev.preventDefault();
    const card = btn.closest('.card'); if (!card) return;
    const date = card.dataset.date; const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📊 ' + date + ' / ' + filename + ' Daily Byte 기록을 삭제할까요?')) return;
    btn.disabled = true; btn.textContent = '⏳';
    fetch('api/daily_byte_delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: filename})
    }).then(function(r) { return r.json().then(function(d) { return {status: r.status, body: d}; }); })
      .then(function(res) {
        if (res.status === 200 && res.body && res.body.ok) {
          card.style.transition = 'opacity 0.2s'; card.style.opacity = '0';
          setTimeout(function() { card.remove(); }, 200);
        } else {
          alert('삭제 실패: ' + (res.body && res.body.error || res.status));
          btn.disabled = false; btn.textContent = '🗑️';
        }
      }).catch(function(err) {
        alert('삭제 실패: ' + err); btn.disabled = false; btn.textContent = '🗑️';
      });
  });
});
</script>
</body></html>
"""


def _load_daily_byte_runs() -> list[dict]:
    """Scan ~/.tradingagents/daily_byte_archive/YYYY-MM-DD/*.json → run
    dicts newest-first. Each: {ts, date, body, cost_krw, elapsed_sec,
    kind, _path, _date, _filename}. All errors swallowed per-file."""
    import json as _json
    runs: list[dict] = []
    if not _DAILY_BYTE_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_DAILY_BYTE_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = json_file.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: daily_byte load %s failed: %s", json_file, exc)
    except Exception as exc:
        log.warning("dashboard: daily_byte archive scan failed: %s", exc)
    return runs


def _render_daily_byte_page(runs: list[dict]) -> str:
    """Render daily_byte.html — date-grouped brief cards. Reuses
    _SCREENER_CSS (theme + card + search-bar + snippet styles) and the
    same scr-* element ids so the look matches the screener archive."""
    import html as _html
    import json as _json_db
    from collections import defaultdict
    from datetime import datetime as _dt_db, timezone as _tz_db, timedelta as _td_db

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    total_runs = len(runs)
    total_cost_krw = sum(r.get("cost_krw", 0) or 0 for r in runs)
    from datetime import datetime as _dt_dbc, timezone as _tz_dbc, timedelta as _td_dbc
    _today_kst_db = _dt_dbc.now(_tz_dbc(_td_dbc(hours=9))).date().isoformat()
    _month_kst_db = _today_kst_db[:7]
    today_cost_krw = sum(
        r.get("cost_krw", 0) or 0 for r in runs if r.get("_date") == _today_kst_db
    )
    month_cost_krw = sum(
        r.get("cost_krw", 0) or 0 for r in runs
        if (r.get("_date") or "").startswith(_month_kst_db)
    )
    weekly_n = sum(1 for r in runs if r.get("kind") == "weekly")

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="screener.html">📊 Bottleneck Screener</a>
    · <a href="http://34.50.23.221:8002/dashboard" target="_blank" rel="noopener">📈 Standard View</a>
    · <a href="http://34.50.23.221:8765/dashboard/" target="_blank" rel="noopener">{_KR_FLAG_SVG} 한국 수출입 데이터</a>
  </div>
  <h1>📊 Daily Byte — Archive</h1>
  <p class="sub">장 마감 후 KR 수급 브리프 · 한국거래일 19:00 Daily + 일 22:00 Weekly (KST) · 수급 데이터 관찰(교육·정보), 투자 권유 아님</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 브리프</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost_krw:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost_krw:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost_krw:,.0f}</div><div class="stat-l">누적 비용</div></div>
    <div class="stat"><div class="stat-v">{weekly_n}</div><div class="stat-l">Weekly 종합</div></div>
  </div>

  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="종목 / 섹터 / 본문 검색 (예: 삼성전자, 외국인, 원전, 로테이션)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 Daily Byte 브리프</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 Daily Byte 기록이 없습니다. 한국거래일 19:00 KST 자동 생성됩니다.
  </div>
</div></body></html>""")
        return "".join(parts)

    _today_kst = _dt_db.now(_tz_db(_td_db(hours=9))).date().isoformat()
    _this_month = _today_kst[:7]  # 'YYYY-MM'

    # 날짜를 월(YYYY-MM)로 묶어 month → [dates] 구조 생성. 이번 달은
    # 펼침, 나머지는 접힘 (사용자 요청 2026-06-01 — 일별뿐 아니라 월별
    # collapse). 날짜·월 모두 내림차순.
    months: dict[str, list[str]] = defaultdict(list)
    for date in sorted(by_date.keys(), reverse=True):
        months[(date or "")[:7]].append(date)

    def _format_month_kr(ym: str) -> str:
        try:
            y, m = ym.split("-")
            return f"{int(y)}년 {int(m)}월"
        except Exception:
            return ym

    for month in sorted(months.keys(), reverse=True):
        month_dates = months[month]
        month_open = " open" if month == _this_month else ""
        month_count = sum(len(by_date[d]) for d in month_dates)
        parts.append(
            f'<details class="month"{month_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_html.escape(_format_month_kr(month))}</span>'
            f'<span class="count">{month_count}건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
        for date in month_dates:
            day_open = " open" if date == _today_kst else ""
            day_count = len(by_date[date])
            parts.append(
                f'<details class="day"{day_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count}건</span>'
                f'</summary>'
                f'<div class="day-body">'
            )
            for r in by_date[date]:
                raw_ts = r.get("ts") or ""
                ts_clock = raw_ts.split("T", 1)[1][:5] if "T" in raw_ts else ""
                ts = _html.escape(ts_clock)
                cost = r.get("cost_krw", 0) or 0
                elapsed = r.get("elapsed_sec", 0) or 0
                kind = r.get("kind", "daily")
                is_weekly = kind == "weekly"
                kind_badge = "📅 Weekly" if is_weekly else "📊 Daily"
                title = f"{kind_badge} · {_html.escape(date)}"
                # Body is already Telegram-safe HTML (<b>/<i> only) from
                # daily_kr_flow._post_process. Strip the leading title line
                # (Python adds '📊 <b>Daily Byte - ...</b>' + <i>subtitle</i>)
                # so the card doesn't double up on the heading.
                body = (r.get("body") or "").strip()
                # 수평선/구분선 줄 제거 (render 시점 — strip-fix 이전에 아카이브된
                # 옛 run 의 '---' / '--- / ---' 도 소급 정리). 단어문자 없이 대시류
                # 2+ 만 있는 줄 + 그로 인한 연속 빈 줄.
                body = re.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
                body = re.sub(r"\n{3,}", "\n\n", body).strip()

                # Per-line snippet index for search (sec='brief'). Strip tags
                # for the searchable text so '<b>' noise doesn't pollute hits.
                plain = re.sub(r"<[^>]+>", "", body)
                card_lines: list[dict] = []
                for ln in plain.splitlines():
                    s = ln.strip()
                    if len(s) >= 3:
                        card_lines.append({"sec": "brief", "txt": s[:300]})
                if len(card_lines) > 200:
                    card_lines = card_lines[:200]
                search_attr = _html.escape(plain.lower()[:6000])
                lines_attr = _html.escape(_json_db.dumps(card_lines, ensure_ascii=False))

                filename = _html.escape(r.get("_filename", ""))
                day_card_count = len(by_date[date])
                card_default_open = (date == _today_kst and day_card_count == 1)
                card_open_attr = " open" if card_default_open else ""
                card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
                # 인포그래픽 이미지 (archive/ 기준 상대경로) — 있으면 카드 상단 임베드
                png_rel = (r.get("png") or "").strip()
                img_html = ""
                if png_rel and re.match(r"^daily_byte_img/[\w.\-]+\.png$", png_rel):
                    img_html = (f'<img class="db-info" src="{_html.escape(png_rel)}" '
                                f'alt="Daily Byte 인포그래픽" loading="lazy" '
                                f'style="width:100%;max-width:680px;border-radius:10px;'
                                f'margin:8px auto 14px;display:block">')

                parts.append(f"""
      <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
        <summary class="card-h">
          <span class="card-toggle">▸</span>
          <span class="domain">{title}</span>
          <span class="meta">⏱ {ts} · ₩{cost:,.1f} · {elapsed:.0f}s</span>
          <button class="del-btn" type="button" title="이 Daily Byte 기록 삭제">🗑️</button>
        </summary>
        <div class="card-body">
          {img_html}
          <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body}</div></div>
        </div>
      </details>
    """)
            parts.append('</div></details>')  # close day
        parts.append('</div></details>')  # close month

    parts.append("</div>")
    parts.append(_DAILY_BYTE_JS)
    return "".join(parts)


def regenerate_daily_byte_index() -> None:
    """Scan daily_byte archive → write daily_byte.html under ARCHIVE_ROOT.
    Called from daily_kr_flow after a run + from _periodic_dashboard_refresh.
    All errors swallowed."""
    try:
        runs = _load_daily_byte_runs()
        html = _render_daily_byte_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "daily_byte.html").write_text(html, encoding="utf-8")
        log.info("dashboard: daily_byte.html regenerated (%d runs)", len(runs))
    except Exception as exc:
        log.warning("dashboard: daily_byte regen failed: %s", exc)


# ── 부동산 Byte archive view ──────────────────────────────────────────────
_REALESTATE_ARCHIVE_DIR = Path.home() / ".tradingagents" / "realestate_archive"
_REALESTATE_JS = _DAILY_BYTE_JS.replace("api/daily_byte_delete", "api/realestate_delete").replace(
    "Daily Byte 브리프", "부동산 Byte").replace("Daily Byte 기록", "부동산 Byte 기록")


def _load_realestate_runs() -> list[dict]:
    import json as _json
    runs: list[dict] = []
    if not _REALESTATE_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_REALESTATE_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for jf in sorted(date_dir.iterdir(), reverse=True):
                if not jf.name.endswith(".json"):
                    continue
                try:
                    with open(jf, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = jf.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: realestate load %s failed: %s", jf, exc)
    except Exception as exc:
        log.warning("dashboard: realestate scan failed: %s", exc)
    return runs


def _render_realestate_page(runs: list[dict]) -> str:
    """Render realestate.html — 부동산 Byte 아카이브 (text brief 카드).
    daily_byte 패턴 mirror (검색·🗑️·테마·오늘/누적 비용)."""
    import html as _html
    import json as _json_r
    import re as _re_r
    from collections import defaultdict
    from datetime import datetime as _dt_r, timezone as _tz_r, timedelta as _td_r

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)
    total = len(runs)
    total_cost = sum(r.get("cost_krw", 0) or 0 for r in runs)
    _today = _dt_r.now(_tz_r(_td_r(hours=9))).date().isoformat()
    _month = _today[:7]
    today_cost = sum(r.get("cost_krw", 0) or 0 for r in runs if r.get("_date") == _today)
    month_cost = sum(r.get("cost_krw", 0) or 0 for r in runs
                     if (r.get("_date") or "").startswith(_month))

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="daily_byte.html">📊 Daily Byte</a>
    · <a href="screener.html">📊 Bottleneck Screener</a>
  </div>
  <h1>🏠 부동산 Byte — Archive</h1>
  <p class="sub">아파트 실거래가(MOLIT) 주간 브리프 · ticker·5거래일과 별개 · 공공데이터 관찰(투자 권유 아님)</p>
  <div class="stats">
    <div class="stat"><div class="stat-v">{total}</div><div class="stat-l">총 브리프</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost:,.0f}</div><div class="stat-l">누적 비용</div></div>
  </div>
  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="지역 / 본문 검색 (예: 강남, 평당, 금리, 전세)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total}건의 부동산 브리프</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")
    if not runs:
        parts.append("""
  <div class="empty">아직 기록이 없습니다. 금 09:00 KST 자동 생성 (DATA_GO_KR_API_KEY 필요).</div>
</div></body></html>""")
        return "".join(parts)

    _this_month = _today[:7]
    _month_counts: dict[str, int] = defaultdict(int)
    for d in by_date:
        _month_counts[(d or "")[:7]] += len(by_date[d])
    _prev_month: str | None = None
    for date in sorted(by_date.keys(), reverse=True):
        _prev_month = _month_transition(
            parts, _prev_month, date, _this_month, _month_counts)
        day_open = " open" if date == _today else ""
        parts.append(
            f'<details class="day"{day_open}><summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{len(by_date[date])}건</span></summary>'
            f'<div class="day-body">')
        for r in by_date[date]:
            body = (r.get("body") or "").strip()
            body = _re_r.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
            ymd = r.get("ymd", "")
            title = f"🏠 {ymd[:4]}.{ymd[4:6]} · {_html.escape(date)}" if ymd else _html.escape(date)
            cost = r.get("cost_krw", 0) or 0
            ts_clock = (r.get("ts") or "").split("T", 1)[-1][:5] if "T" in (r.get("ts") or "") else ""
            filename = _html.escape(r.get("_filename", ""))
            plain = _re_r.sub(r"<[^>]+>", "", body)
            lines = [{"sec": "brief", "txt": s.strip()} for s in plain.splitlines() if len(s.strip()) >= 3][:200]
            search_attr = _html.escape(plain.lower()[:5000])
            lines_attr = _html.escape(_json_r.dumps(lines, ensure_ascii=False))
            card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
            png_rel = (r.get("png") or "").strip()
            img_html = ""
            if png_rel and _re_r.match(r"^realestate_img/[\w.\-]+\.png$", png_rel):
                img_html = (f'<img src="{_html.escape(png_rel)}" alt="부동산 인포그래픽" '
                            f'loading="lazy" style="width:100%;max-width:680px;'
                            f'border-radius:10px;margin:8px auto 14px;display:block">')
            parts.append(f"""
  <details class="card" id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="false">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{title}</span>
      <span class="meta">⏱ {_html.escape(ts_clock)} · ₩{cost:,.1f}</span>
      <button class="del-btn" type="button" title="이 브리프 삭제">🗑️</button>
    </summary>
    <div class="card-body">
      {img_html}
      <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body}</div></div>
    </div>
  </details>
""")
        parts.append('</div></details>')
    _month_close(parts, _prev_month)
    parts.append("</div>")
    parts.append(_REALESTATE_JS)
    return "".join(parts)


def regenerate_realestate_index() -> None:
    """Scan realestate archive → write realestate.html under ARCHIVE_ROOT."""
    try:
        runs = _load_realestate_runs()
        html = _render_realestate_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "realestate.html").write_text(html, encoding="utf-8")
        log.info("dashboard: realestate.html regenerated (%d runs)", len(runs))
    except Exception as exc:
        log.warning("dashboard: realestate regen failed: %s", exc)


# ── 청약 Byte archive view (신규 분양 모집공고 daily 피드) ────────────────
_CHEONGYAK_ARCHIVE_DIR = Path.home() / ".tradingagents" / "cheongyak_archive"
_CHEONGYAK_JS = _DAILY_BYTE_JS.replace("api/daily_byte_delete", "api/cheongyak_delete").replace(
    "Daily Byte 브리프", "청약 Byte").replace("Daily Byte 기록", "청약 Byte 기록")


def _load_cheongyak_runs() -> list[dict]:
    import json as _json
    runs: list[dict] = []
    if not _CHEONGYAK_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_CHEONGYAK_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for jf in sorted(date_dir.iterdir(), reverse=True):
                if not jf.name.endswith(".json"):
                    continue
                try:
                    with open(jf, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_date"] = date_dir.name
                    rec["_filename"] = jf.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: cheongyak load %s failed: %s", jf, exc)
    except Exception as exc:
        log.warning("dashboard: cheongyak scan failed: %s", exc)
    return runs


def _render_cheongyak_page(runs: list[dict]) -> str:
    """Render cheongyak.html — 청약 Byte 아카이브 (신규 분양 피드 카드)."""
    import html as _html
    import json as _json_r
    import re as _re_r
    from collections import defaultdict
    from datetime import datetime as _dt_r, timezone as _tz_r, timedelta as _td_r

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)
    total = len(runs)
    total_cost = sum(r.get("cost_krw", 0) or 0 for r in runs)
    _today = _dt_r.now(_tz_r(_td_r(hours=9))).date().isoformat()
    _month = _today[:7]
    today_cost = sum(r.get("cost_krw", 0) or 0 for r in runs if r.get("_date") == _today)
    month_cost = sum(r.get("cost_krw", 0) or 0 for r in runs
                     if (r.get("_date") or "").startswith(_month))

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="daily_byte.html">📊 Daily Byte</a>
    · <a href="realestate.html">🏠 부동산</a>
  </div>
  <h1>🎟️ 청약 Byte — Archive</h1>
  <p class="sub">신규 아파트 분양 모집공고(청약홈) daily 피드 · ticker·5거래일과 별개 · 공공데이터 관찰(청약 권유 아님)</p>
  <div class="stats">
    <div class="stat"><div class="stat-v">{total}</div><div class="stat-l">총 피드</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost:,.0f}</div><div class="stat-l">누적 비용</div></div>
  </div>
  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="지역 / 단지명 검색 (예: 강남, 동탄, 신혼희망)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total}건의 청약 피드</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")
    if not runs:
        parts.append("""
  <div class="empty">아직 기록이 없습니다. 평일 10:00 KST 자동 생성 (DATA_GO_KR_API_KEY 필요).</div>
</div></body></html>""")
        return "".join(parts)

    _this_month = _today[:7]
    _month_counts: dict[str, int] = defaultdict(int)
    for d in by_date:
        _month_counts[(d or "")[:7]] += len(by_date[d])
    _prev_month: str | None = None
    for date in sorted(by_date.keys(), reverse=True):
        _prev_month = _month_transition(
            parts, _prev_month, date, _this_month, _month_counts)
        day_open = " open" if date == _today else ""
        parts.append(
            f'<details class="day"{day_open}><summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{len(by_date[date])}건</span></summary>'
            f'<div class="day-body">')
        for r in by_date[date]:
            body = (r.get("body") or "").strip()
            body = _re_r.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
            cnt = r.get("count", 0) or 0
            title = f"🎟️ 신규 분양 {cnt}건 · {_html.escape(date)}"
            cost = r.get("cost_krw", 0) or 0
            ts_clock = (r.get("ts") or "").split("T", 1)[-1][:5] if "T" in (r.get("ts") or "") else ""
            filename = _html.escape(r.get("_filename", ""))
            plain = _re_r.sub(r"<[^>]+>", "", body)
            lines = [{"sec": "feed", "txt": s.strip()} for s in plain.splitlines() if len(s.strip()) >= 3][:300]
            search_attr = _html.escape(plain.lower()[:6000])
            lines_attr = _html.escape(_json_r.dumps(lines, ensure_ascii=False))
            card_id = f"card-{_html.escape(r.get('_date',''))}-{filename}".replace(".", "_")
            parts.append(f"""
  <details class="card" id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="false">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{title}</span>
      <span class="meta">⏱ {_html.escape(ts_clock)} · ₩{cost:,.1f}</span>
      <button class="del-btn" type="button" title="이 피드 삭제">🗑️</button>
    </summary>
    <div class="card-body">
      <div class="analysis-sec"><div class="analysis-b" data-section="feed">{body}</div></div>
    </div>
  </details>
""")
        parts.append('</div></details>')
    _month_close(parts, _prev_month)
    parts.append("</div>")
    parts.append(_CHEONGYAK_JS)
    return "".join(parts)


def regenerate_cheongyak_index() -> None:
    """Scan cheongyak archive → write cheongyak.html under ARCHIVE_ROOT."""
    try:
        runs = _load_cheongyak_runs()
        html = _render_cheongyak_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "cheongyak.html").write_text(html, encoding="utf-8")
        log.info("dashboard: cheongyak.html regenerated (%d runs)", len(runs))
    except Exception as exc:
        log.warning("dashboard: cheongyak regen failed: %s", exc)


# ── 미국 레딧 게시물 분석 — t.me/insidertracking forward archive ─────────
# bot/reddit_insider_watch.py 가 5분 폴링으로 '미국 레딧 게시물 분석' 제목
# 메시지만 수집 → 우리 채널 forward + archive 저장. 본 페이지는 그 archive
# 를 Daily Byte 와 같은 형식(월/일 collapse + 검색 + 카드)으로 surface.
# 비용 ₩0 (LLM 없음, 원본 그대로 forward).
_REDDIT_INSIDER_ARCHIVE_DIR = (
    Path.home() / ".tradingagents" / "reddit_insider_archive"
)


def _load_reddit_insider_runs() -> list[dict]:
    """Scan ~/.tradingagents/reddit_insider_archive/YYYY-MM-DD/*.json →
    record dicts newest-first. 각 dict: {ts, _date, msg_id, title, body,
    source, _path, _filename}. 모든 per-file 에러 swallow."""
    import json as _json
    runs: list[dict] = []
    if not _REDDIT_INSIDER_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(
            _REDDIT_INSIDER_ARCHIVE_DIR.iterdir(), reverse=True
        ):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = rec.get("_date") or date_dir.name
                    rec["_filename"] = json_file.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning(
                        "dashboard: reddit_insider load %s failed: %s",
                        json_file, exc,
                    )
    except Exception as exc:
        log.warning("dashboard: reddit_insider scan failed: %s", exc)
    return runs


def _render_reddit_insider_page(runs: list[dict]) -> str:
    """Render reddit_insider.html — Daily Byte 패턴 mirror (월/일 collapse +
    검색 + 카드). cost 미사용(LLM 없음 → 항상 ₩0)이라 stat 은 총건수 / 가장
    최근 only. body 는 원본 그대로(plain text) — Telegram 첫 줄을 카드 제목,
    전체를 카드 본문으로."""
    import html as _html
    import json as _json_ri
    from collections import defaultdict
    from datetime import datetime as _dt_ri, timezone as _tz_ri, timedelta as _td_ri

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    total_runs = len(runs)
    last_ts = ""
    if runs:
        # runs 는 newest-first 정렬돼 들어옴
        last_ts = (runs[0].get("ts") or "")[:16].replace("T", " ")

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="daily_byte.html">📊 Daily Byte</a>
    · <a href="screener.html">📊 Bottleneck Screener</a>
  </div>
  <h1>📨 미국 레딧 게시물 분석 — Archive</h1>
  <p class="sub">t.me/insidertracking 자동 포워드 · 제목 '미국 레딧 게시물 분석' 필터 · ₩0 (LLM 없음, 원본 그대로) · 정보 관찰(투자 권유 아님)</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 포워드</div></div>
    <div class="stat"><div class="stat-v">{_html.escape(last_ts) if last_ts else '—'}</div><div class="stat-l">마지막 수신 (KST)</div></div>
    <div class="stat"><div class="stat-v">₩0</div><div class="stat-l">누적 비용</div></div>
  </div>

  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="제목 / 본문 / 종목 검색 (예: SPCE, 광기, bagholder)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 포워드</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 포워드 기록이 없습니다. t.me/insidertracking 채널의 '미국 레딧 게시물 분석' 게시물이 올라오면 5분 내 자동 수집됩니다.
  </div>
</div></body></html>""")
        return "".join(parts)

    _today_kst_ri = _dt_ri.now(_tz_ri(_td_ri(hours=9))).date().isoformat()
    _this_month_ri = _today_kst_ri[:7]

    # 월 → 일 → 카드 (Daily Byte 와 동일 구조).
    months: dict[str, list[str]] = defaultdict(list)
    for date in sorted(by_date.keys(), reverse=True):
        months[(date or "")[:7]].append(date)

    def _format_month_ri(ym: str) -> str:
        try:
            y, m = ym.split("-")
            return f"{int(y)}년 {int(m)}월"
        except Exception:
            return ym

    for month in sorted(months.keys(), reverse=True):
        month_dates = months[month]
        month_open = " open" if month == _this_month_ri else ""
        month_count = sum(len(by_date[d]) for d in month_dates)
        parts.append(
            f'<details class="month"{month_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_html.escape(_format_month_ri(month))}</span>'
            f'<span class="count">{month_count}건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
        for date in month_dates:
            day_open = " open" if date == _today_kst_ri else ""
            day_count = len(by_date[date])
            parts.append(
                f'<details class="day"{day_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count}건</span>'
                f'</summary>'
                f'<div class="day-body">'
            )
            for r in by_date[date]:
                raw_ts = r.get("ts") or ""
                ts_clock = raw_ts.split("T", 1)[1][:5] if "T" in raw_ts else ""
                ts_html = _html.escape(ts_clock)
                title = _html.escape(r.get("title") or "미국 레딧 게시물 분석")
                body_raw = (r.get("body") or "").strip()
                # plain text (Telegram body 가 HTML 아님) → escape + <br>.
                body_html = _html.escape(body_raw).replace("\n", "<br>")
                plain = body_raw
                card_lines: list[dict] = []
                for ln in plain.splitlines():
                    s = ln.strip()
                    if len(s) >= 3:
                        card_lines.append({"sec": "brief", "txt": s[:300]})
                if len(card_lines) > 200:
                    card_lines = card_lines[:200]
                search_attr = _html.escape(plain.lower()[:6000])
                lines_attr = _html.escape(
                    _json_ri.dumps(card_lines, ensure_ascii=False)
                )

                filename = _html.escape(r.get("_filename", ""))
                card_default_open = (
                    date == _today_kst_ri and day_count == 1
                )
                card_open_attr = " open" if card_default_open else ""
                card_id = (
                    f"card-{_html.escape(r.get('_date', ''))}-{filename}"
                    .replace(".", "_")
                )

                parts.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date', ''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">📨 {title}</span>
      <span class="meta">⏱ {ts_html}</span>
    </summary>
    <div class="card-body">
      <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body_html}</div></div>
    </div>
  </details>
""")
            parts.append('</div></details>')  # close day
        parts.append('</div></details>')  # close month

    parts.append("</div>")
    parts.append(_DAILY_BYTE_JS)
    return "".join(parts)


def regenerate_reddit_insider_index() -> None:
    """Scan reddit_insider archive → write reddit_insider.html under
    ARCHIVE_ROOT. Called from bot.reddit_insider_watch after new forwards
    + from periodic dashboard refresh + on startup."""
    try:
        runs = _load_reddit_insider_runs()
        html = _render_reddit_insider_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "reddit_insider.html").write_text(
            html, encoding="utf-8"
        )
        log.info(
            "dashboard: reddit_insider.html regenerated (%d runs)",
            len(runs),
        )
    except Exception as exc:
        log.warning("dashboard: reddit_insider regen failed: %s", exc)


# ── Watchlist 조건 알림 대시보드 (2026-06-04) ────────────────────────────
def _render_watchlist_page(watches: list[dict], alerts: list[dict]) -> str:
    """watchlist.html — 활성 워치 테이블 + 알림 이력. 읽기 전용
    (등록/삭제는 텔레그램 /watch · /unwatch).

    CSS 는 _DETAIL_CSS(순수 CSS 문자열) 를 <style> 안에 사용 — _SCREENER_CSS
    는 <!DOCTYPE…<style>…</style></head><body> 전체 문서라 <style> 안에 넣으면
    내부 </style> 가 블록을 조기 종료해 뒤따르는 table CSS 가 텍스트로 누수됨
    (2026-06-05 버그). _DETAIL_CSS 는 .wrap/.back/.sub/h1/h2/code + --border/
    --fg-soft 테마 vars 를 모두 제공하므로 table CSS 도 정상 적용."""
    def esc(s):
        return _html.escape(str(s))

    watch_rows = ""
    for w in watches:
        conds = " ".join(w.get("conditions") or [])
        added = (w.get("added") or "")[:16].replace("T", " ")
        watch_rows += (
            f"<tr><td><b>{esc(w.get('ticker',''))}</b></td>"
            f"<td><code>{esc(conds)}</code></td>"
            f"<td class='muted'>{esc(added)}</td>"
            f"<td class='muted'>{esc(w.get('id',''))}</td></tr>"
        )
    if not watch_rows:
        watch_rows = "<tr><td colspan='4' class='muted'>감시 중인 종목 없음 — 텔레그램에서 <code>/watch TICKER 조건</code></td></tr>"

    alert_rows = ""
    for a in alerts:
        ts = (a.get("ts") or "")[:16].replace("T", " ")
        hits = " · ".join(a.get("hits") or [])
        alert_rows += (
            f"<tr><td class='muted'>{esc(ts)}</td>"
            f"<td><b>{esc(a.get('ticker',''))}</b></td>"
            f"<td>{esc(hits)}</td></tr>"
        )
    if not alert_rows:
        alert_rows = "<tr><td colspan='3' class='muted'>아직 발생한 알림 없음</td></tr>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🔔 워치리스트</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}
table {{ width:100%; border-collapse:collapse; margin:10px 0 24px; font-size:14px; }}
th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--border); vertical-align:top; }}
th {{ color:var(--fg-soft); font-weight:600; font-size:12px; }}
td.muted, .muted {{ color:var(--fg-soft); }}
code {{ font-family:'IBM Plex Mono',monospace; }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="./index.html">← 아카이브로 돌아가기</a>
  <h1>🔔 워치리스트</h1>
  <p class="sub">조건 충족 시 텔레그램 알림 (30분 간격 체크 · LLM 0 · 비용 ₩0). 등록/삭제는 텔레그램 <code>/watch</code> · <code>/unwatch</code>.</p>
  <h2>📋 활성 워치 ({len(watches)})</h2>
  <table>
    <thead><tr><th>종목</th><th>조건</th><th>등록</th><th>id</th></tr></thead>
    <tbody>{watch_rows}</tbody>
  </table>
  <h2>🔔 알림 이력 ({len(alerts)})</h2>
  <table>
    <thead><tr><th>시각</th><th>종목</th><th>충족 조건</th></tr></thead>
    <tbody>{alert_rows}</tbody>
  </table>
  <p class="sub">조건: <code>rsi&lt;30 rsi&gt;70 price&gt;X price&lt;X &gt;sma50 &lt;sma200 52whigh 52wlow earnings foreignbuy foreignsell instbuy instsell</code></p>
</div>
</body>
</html>
"""


def regenerate_watchlist_index() -> None:
    """Scan watchlist + alerts → write watchlist.html under ARCHIVE_ROOT."""
    try:
        from bot.watchlist import all_watches, load_alerts
        watches = all_watches()
        alerts = load_alerts(200)
        html = _render_watchlist_page(watches, alerts)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "watchlist.html").write_text(html, encoding="utf-8")
        log.info("dashboard: watchlist.html regenerated (%d watches, %d alerts)",
                 len(watches), len(alerts))
    except Exception as exc:
        log.warning("dashboard: watchlist regen failed: %s", exc)


# ── 페이퍼 트레이딩 대시보드 (E0, 2026-06-05) — paper.html ──────────────────
# bot/paper_trading.py 의 모의 계좌(포지션·P&L)를 읽어 렌더. 명령(/paper*) 시
# + startup + 자정 regen. 실거래 실행 골격의 read-only surface (docs/execution_
# architecture.md E0). _SCREENER_CSS 페이지-opener + _PF_CSS 재사용.
def _paper_nav(active: str = "paper") -> str:
    def _a(href, label, key):
        return f"<b>{label}</b>" if key == active else f'<a href="{href}">{label}</a>'
    return (
        '<div class="nav">'
        + _a("portfolio.html", "💼 자산", "portfolio")
        + ' · ' + _a("budget.html", "📒 가계부", "budget")
        + ' · <a href="index.html">🦉 NOAH 종목분석</a>'
        + ' · <a href="watchlist.html">🔔 워치리스트</a>'
        + ' · ' + _a("paper.html", "🧪 페이퍼", "paper")
        + '</div>')


def _render_paper_page(summ: dict) -> str:
    import html as _html

    def _native(v, currency):
        if v is None:
            return "—"
        dec = 2 if currency == "USD" else 0
        return f"{_sym_cur(currency)}{v:,.{dec}f}"

    nav = _paper_nav("paper")
    rows = summ.get("rows", [])
    trades = summ.get("trades", [])
    # Risk Gate 상태 + kill-switch 배너 (E0.5) + E1 모드.
    gate_line, halt_banner, e1_banner = "", "", ""
    try:
        from bot.risk_gate import status_line, halt_active
        from bot import paper_trading as _pt
        _auto = "ON" if _pt.auto_enabled() else "OFF"
        _e1 = _pt.e1_mode()
        e1_tag = " · 🔗 KIS 모의투자" if _e1 else ""
        gate_line = ('<p class="sub" style="color:var(--muted);font-size:12px">🛡️ '
                     + _html.escape(status_line())
                     + f' · 🤖 자동매매 {_auto} (사이징 {_pt.auto_size_pct():.0%})'
                     + e1_tag + '</p>')
        if halt_active():
            halt_banner = ('<div class="pf-card" style="border-color:#e2574c">'
                           '<b style="color:#e2574c">⛔ 거래 중지(kill-switch) 활성</b> — '
                           '신규 매수 차단(매도/청산은 허용). 텔레그램 '
                           '<code>/paper resume</code> 으로 해제.</div>')
        if _e1:
            e1_banner = ('<div class="pf-card" style="border-color:#2563eb">'
                         '<b style="color:#2563eb">🔗 KIS 모의투자 연결</b> — '
                         'KR+US 종목 KIS 서버 체결(슬리피지·장중 제한 반영).</div>')
    except Exception:
        pass

    if not rows and not trades:
        return _SCREENER_CSS + _PF_CSS + (
            '<div class="wrap">' + nav + '<h1>🧪 페이퍼 트레이딩</h1>'
            '<p class="sub">아직 모의 거래가 없습니다. 텔레그램에서 '
            '<b>/paper buy TICKER 수량</b> 으로 시작하세요. (모의·돈 0)</p>'
            + halt_banner + e1_banner + gate_line + '</div>')

    part = "" if summ.get("priced_all") else (
        ' <span style="color:var(--muted)">(일부 현재가 미조회 — 평가/총자산 부분값)</span>')
    stats = (
        '<div class="stats">'
        f'<div class="stat"><div class="stat-num">{_pf_won(summ["total_equity_krw"])}</div>'
        f'<div class="stat-lbl">총자산{part}</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(summ["cash_krw"])}</div>'
        '<div class="stat-lbl">현금</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(summ["positions_value_krw"])}</div>'
        '<div class="stat-lbl">포지션 평가</div></div>'
        f'<div class="stat"><div class="stat-num" style="color:{_pf_col(summ["unrealized_pnl_krw"])}">'
        f'{_pf_won(summ["unrealized_pnl_krw"])}</div><div class="stat-lbl">미실현 손익</div></div>'
        f'<div class="stat"><div class="stat-num" style="color:{_pf_col(summ["realized_pnl_krw"])}">'
        f'{_pf_won(summ["realized_pnl_krw"])}</div><div class="stat-lbl">실현 손익</div></div>'
        f'<div class="stat"><div class="stat-num" style="color:{_pf_col(summ["total_return_pct"])}">'
        f'{summ["total_return_pct"]:+.2f}%</div><div class="stat-lbl">총 수익률</div></div>'
        '</div>')

    pos_html = ""
    for r in rows:
        cur = r.get("currency")
        _hz = r.get("auto_close_date")
        _hz_badge = (f' <span style="color:#b07cff;font-size:11px">🤖~{_html.escape(str(_hz))}</span>'
                     if _hz else "")
        pos_html += (
            f'<tr><td><b>{_html.escape(r["ticker"])}</b>{_hz_badge}</td>'
            f'<td>{_html.escape(str(r.get("market") or ""))}</td>'
            f'<td class="r">{r["qty"]:g}</td>'
            f'<td class="r">{_native(r.get("avg_cost_native"), cur)}</td>'
            f'<td class="r">{_native(r.get("cur_native"), cur)}</td>'
            f'<td class="r">{_pf_won(r.get("value_krw"))}</td>'
            f'<td class="r" style="color:{_pf_col(r.get("unrealized_krw"))}">{_pf_won(r.get("unrealized_krw"))}</td>'
            f'<td class="r" style="color:{_pf_col(r.get("ret_pct"))}">'
            f'{("%+.1f%%" % r["ret_pct"]) if r.get("ret_pct") is not None else "—"}</td></tr>')
    if not pos_html:
        pos_html = '<tr><td colspan="8" class="muted">보유 포지션 없음</td></tr>'
    pos_block = (
        '<div class="pf-card"><div class="pf-h">보유 포지션 (' + str(summ.get("n_positions", 0)) + ')</div>'
        '<table class="pf-tbl"><thead><tr><th>종목</th><th>시장</th><th class="r">수량</th>'
        '<th class="r">평단</th><th class="r">현재가</th><th class="r">평가(₩)</th>'
        '<th class="r">미실현(₩)</th><th class="r">수익률</th></tr></thead>'
        '<tbody>' + pos_html + '</tbody></table></div>')

    tr_html = ""
    import datetime as _dt
    for t in reversed(trades[-50:]):
        ts = t.get("ts")
        when = (_dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "")
        side = "매수" if t.get("side") == "buy" else "매도"
        scol = "#16a34a" if t.get("side") == "buy" else "#dc2626"
        realized = t.get("realized_krw")
        tr_html += (
            f'<tr><td class="muted">{_html.escape(when)}</td>'
            f'<td><b>{_html.escape(t.get("ticker",""))}</b></td>'
            f'<td style="color:{scol}">{side}</td>'
            f'<td class="r">{t.get("qty",0):g}</td>'
            f'<td class="r">{_native(t.get("fill_native"), t.get("currency"))}</td>'
            f'<td class="r">{_pf_won(t.get("krw"))}</td>'
            f'<td class="r" style="color:{_pf_col(realized)}">'
            f'{_pf_won(realized) if realized is not None else "—"}</td></tr>')
    if not tr_html:
        tr_html = '<tr><td colspan="7" class="muted">체결 내역 없음</td></tr>'
    tr_block = (
        '<div class="pf-card"><div class="pf-h">체결 내역 (최근 50)</div>'
        '<table class="pf-tbl"><thead><tr><th>시각</th><th>종목</th><th>구분</th>'
        '<th class="r">수량</th><th class="r">체결가</th><th class="r">금액(₩)</th>'
        '<th class="r">실현손익(₩)</th></tr></thead><tbody>' + tr_html + '</tbody></table></div>')

    note = (
        '<p class="sub" style="margin-top:14px;color:var(--muted);font-size:12px">'
        f'⚠️ <b>모의(페이퍼) 거래</b> — 실제 주문·실제 돈 아님. 시작 자본 '
        f'₩{int(summ.get("starting_capital_krw", 0)):,}. 시장가 즉시 체결(글리치-가드 현재가), '
        'US 는 체결 시점 USD/KRW 환산. 교육 목적 — 투자 권유 아님.</p>')

    _st = summ.get("stats") or {}
    stats_extra = ""
    if _st.get("n_closes"):
        _wr = _st.get("win_rate")
        stats_extra = ('<p class="sub" style="font-size:12px">📈 거래 '
                       + str(_st["n_closes"]) + '회'
                       + (f' · 승률 {_wr:.0f}% (승 {_st.get("wins",0)}/패 {_st.get("losses",0)})'
                          f' · 평균 실현 {_pf_won(_st.get("avg_realized_krw"))}'
                          if _wr is not None else '') + '</p>')

    # 자산 추이 커브 (E0.5d) — 일별 equity 포인트를 인라인 SVG 폴리라인으로
    # (외부 라이브러리 0). 2점 이상일 때만. y축 반전(SVG 좌표) 주의.
    curve_html = ""
    _hist = [h for h in (summ.get("equity_history") or [])
             if isinstance(h.get("equity_krw"), (int, float))]
    if len(_hist) >= 2:
        vals = [float(h["equity_krw"]) for h in _hist]
        mn, mx = min(vals), max(vals)
        rng = (mx - mn) or 1.0
        W, H = 600.0, 70.0
        pts = " ".join(
            f"{i / (len(vals) - 1) * W:.1f},{H - (v - mn) / rng * H:.1f}"
            for i, v in enumerate(vals))
        col = "#26a69a" if vals[-1] >= vals[0] else "#e2574c"
        curve_html = (
            '<div class="pf-card"><div class="pf-h">자산 추이 (' + str(len(_hist)) + '일)</div>'
            f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" height="{H:.0f}" '
            'preserveAspectRatio="none" style="display:block">'
            f'<polyline fill="none" stroke="{col}" stroke-width="2" points="{pts}"/></svg>'
            f'<p class="sub" style="font-size:11px">{_html.escape(_hist[0]["date"])} '
            f'{_pf_won(vals[0])} → {_html.escape(_hist[-1]["date"])} {_pf_won(vals[-1])}</p></div>')

    # 지정가 대기 주문 (E0.5d limit orders).
    _pend = summ.get("pending") or []
    pend_block = ""
    if _pend:
        prows = ""
        for p in _pend:
            sym = {"KRW": "₩", "USD": "$"}.get(p.get("currency"), "")
            prows += (f'<tr><td>{"매수" if p.get("side") == "buy" else "매도"}</td>'
                      f'<td><b>{_html.escape(p.get("ticker", ""))}</b></td>'
                      f'<td class="r">{p.get("qty", 0):g}</td>'
                      f'<td class="r">{sym}{p.get("limit", 0):,.2f}</td>'
                      f'<td class="muted">{_html.escape(p.get("id", ""))}</td></tr>')
        pend_block = (
            '<div class="pf-card"><div class="pf-h">⏳ 지정가 대기 (' + str(len(_pend)) + ')</div>'
            '<table class="pf-tbl"><thead><tr><th>구분</th><th>종목</th><th class="r">수량</th>'
            '<th class="r">지정가</th><th>id</th></tr></thead><tbody>' + prows
            + '</tbody></table><p class="sub" style="font-size:11px">가격 도달 시 ~30분 내 '
            '자동 체결. 취소: 텔레그램 <code>/paper cancel id</code></p></div>')

    # 자동매매 결정 체인 이력 (추적성, E0.5e) — auto_audit.jsonl 최근 10건.
    # RM/트레이더/PM/분석가 다수/discipline 을 한눈에(어느 매니저가 뭘 말했나).
    audit_block = ""
    try:
        import datetime as _dt
        import json as _j
        _ap = Path.home() / ".tradingagents" / "paper" / "auto_audit.jsonl"
        if _ap.exists():
            arows = ""
            for ln in reversed(_ap.read_text(encoding="utf-8").splitlines()[-10:]):
                try:
                    r = _j.loads(ln)
                except Exception:
                    continue
                c = r.get("chain") or {}
                when = (_dt.datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M")
                        if r.get("ts") else "")
                maj = {"buy": "매수", "sell": "매도", "hold": "보유"}.get(
                    c.get("majority"), c.get("majority") or "")
                chain_txt = " · ".join(p for p in [
                    f"RM {c.get('rm')}" if c.get("rm") else "",
                    f"Tr {c.get('trader')}" if c.get("trader") else "",
                    f"PM {c.get('pm')}" if c.get("pm") else "",
                    f"다수 {maj}" if maj else "",
                    "⚖️강제보정" if c.get("discipline_forced") else "",
                ] if p)
                arows += (f'<tr><td class="muted">{_html.escape(when)}</td>'
                          f'<td><b>{_html.escape(r.get("ticker", ""))}</b></td>'
                          f'<td>{_html.escape(chain_txt)}</td>'
                          f'<td class="muted">{_html.escape(str(r.get("outcome", "")))}</td></tr>')
            if arows:
                audit_block = (
                    '<div class="pf-card"><div class="pf-h">🤖 자동매매 결정 이력 (최근)</div>'
                    '<table class="pf-tbl"><thead><tr><th>시각</th><th>종목</th>'
                    '<th>결정 체인 (RM·트레이더·PM·분석가 다수)</th><th>결과</th></tr></thead>'
                    '<tbody>' + arows + '</tbody></table></div>')
    except Exception:
        pass

    return (_SCREENER_CSS + _PF_CSS + '<div class="wrap">' + nav
            + '<h1>🧪 페이퍼 트레이딩</h1>'
            '<p class="sub">NOAH 분석 신호/수동 명령의 모의 매매 — 실거래 연결 전 전략 검증(리스크 0)</p>'
            + halt_banner + e1_banner + stats + stats_extra + curve_html + pos_block + pend_block
            + tr_block + audit_block + note + gate_line + "</div>")


def _sym_cur(currency: str) -> str:
    return {"KRW": "₩", "USD": "$"}.get(currency, "")


# ── 조건부 스크리너 대시보드 (screen.html) — 2026-06-08 ────────────────────


def _render_screen_page(archives: list[dict]) -> str:
    """screen.html — 조건부 스크리너 결과 이력 + 결과 테이블."""
    cards = ""
    for a in archives[:50]:
        raw_conds = _html.unescape(a.get("conditions_display", a.get("conditions", "")))
        conds = _html.escape(raw_conds)
        ts = (a.get("ts") or "")[:16].replace("T", " ")
        hit_count = a.get("hit_count", 0)
        total = a.get("total_universe", 0)
        elapsed = a.get("elapsed_sec", 0)
        cached = " 💾캐시" if a.get("was_cached") else ""
        date_str = a.get("_date", "")
        filename = a.get("_file", "")
        market = a.get("market", "KR")
        is_us = market == "US"

        hits = a.get("hits", [])
        rows = ""
        for h in hits:
            name = _html.escape(str(h.get("name", "")))
            ticker = _html.escape(str(h.get("ticker", "")))
            mkt = _html.escape(str(h.get("market", "")))
            mcap = h.get("mcap")
            if is_us:
                mcap_str = (f"${mcap/1000:.1f}B" if mcap and mcap >= 1000
                            else f"${mcap:,.0f}M" if mcap else "—")
            else:
                mcap_str = f"{mcap:,.0f}" if mcap else "—"

            extra_vals = []
            for k, v in h.items():
                if k in ("code", "ticker", "name", "market", "mcap", "price"):
                    continue
                if v is not None:
                    extra_vals.append(f"{k}:{v:g}" if isinstance(v, (int, float)) else f"{k}:{v}")
            extra = _html.escape(" · ".join(extra_vals[:5]))
            name_cell = f"<b>{name}</b>" if name else f"<b>{ticker}</b>"
            rows += (
                f"<tr><td>{name_cell}</td>"
                f"<td><code>{ticker}</code></td>"
                f"<td class='muted'>{mkt}</td>"
                f"<td style='text-align:right'>{mcap_str}</td>"
                f"<td class='muted'>{extra}</td></tr>"
            )

        table = ""
        mcap_hdr = "시총($M)" if is_us else "시총(억)"
        if rows:
            table = (
                f"<table><thead><tr><th>종목명</th><th>티커</th><th>시장</th>"
                f"<th>{mcap_hdr}</th><th>지표</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )

        market_badge = " 🇺🇸" if is_us else ""
        cards += f"""<details data-date="{_html.escape(date_str)}" data-file="{_html.escape(filename)}">
<summary><b>{conds}</b>{market_badge} — {hit_count}종목/{total:,}종목{cached}
<span class='muted'>{ts} · {elapsed:.1f}초</span>
<button class="del-btn" type="button" title="삭제">🗑️</button></summary>
{table}
</details>
"""

    if not cards:
        cards = "<p class='muted'>아직 실행 기록 없음 — 텔레그램에서 <code>/screen PER&lt;15 PBR&lt;1</code></p>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 조건부 스크리너</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}
table {{ width:100%; border-collapse:collapse; margin:10px 0 16px; font-size:13px; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); vertical-align:top; }}
th {{ color:var(--fg-soft); font-weight:600; font-size:12px; }}
td.muted, .muted {{ color:var(--fg-soft); font-size:12px; }}
details {{ margin:12px 0; padding:8px; border:1px solid var(--border); border-radius:6px; }}
summary {{ cursor:pointer; font-size:14px; position:relative; }}
summary .muted {{ font-size:12px; margin-left:8px; }}
.del-btn {{ background:none; border:none; cursor:pointer; font-size:14px; float:right; opacity:0.4; }}
.del-btn:hover {{ opacity:1; }}
code {{ font-family:'IBM Plex Mono',monospace; }}
</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="./index.html">← 아카이브로 돌아가기</a>
  · <a href="portfolio.html">💼 자산</a>
  <h1>📊 조건부 스크리너</h1>
  <p class="sub">정량 조건으로 KR + US 종목 필터 (pykrx/yfinance, ₩0).
  텔레그램: <code>/screen PER&lt;15 PBR&lt;1</code> · <code>/screen us PER&lt;15</code> · <code>/screen valueup</code> · <code>/screen list</code></p>
  {{cards}}
</div>
<script>
document.querySelectorAll('.del-btn').forEach(function(btn) {{
  btn.addEventListener('click', function(e) {{
    e.stopPropagation();
    e.preventDefault();
    var det = btn.closest('details');
    if (!det) return;
    if (!confirm('이 기록을 삭제할까요?')) return;
    var date = det.dataset.date;
    var file = det.dataset.file;
    fetch('api/screen_delete', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{date: date, filename: file}})
    }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
      if (d.ok) {{ det.style.opacity = '0.3'; setTimeout(function() {{ det.remove(); }}, 400); }}
      else {{ alert('삭제 실패: ' + (d.error || '')); }}
    }}).catch(function(err) {{ alert('삭제 실패: ' + err); }});
  }});
}});
</script>
</body>
</html>
""".replace("{cards}", cards)


def regenerate_screen_index() -> None:
    """조건부 스크리너 아카이브 → screen.html. startup/실행 후 호출."""
    try:
        from bot.stock_screener import load_screen_archives
        archives = load_screen_archives()
        html_str = _render_screen_page(archives)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "screen.html").write_text(html_str, encoding="utf-8")
        log.info("dashboard: screen.html regenerated (%d archives)", len(archives))
    except Exception as exc:
        log.warning("dashboard: screen regen failed: %s", exc)


def regenerate_paper_index() -> None:
    """페이퍼 계좌 → paper.html. 명령/startup/자정 regen 에서 호출. 에러 무해."""
    try:
        from bot import paper_trading
        summ = paper_trading.summary()
        # 오늘 자산 포인트 기록(일별 1점, dedupe) → equity 커브가 매 액션마다
        # 최신. 스냅샷 후 history 를 다시 읽어 이번 렌더에 오늘 점 포함.
        try:
            paper_trading.snapshot_equity(summ.get("total_equity_krw"))
            summ["equity_history"] = paper_trading.get_account().get("equity_history", [])
        except Exception:
            pass
        html = _render_paper_page(summ)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "paper.html").write_text(html, encoding="utf-8")
        log.info("dashboard: paper.html regenerated (%d positions)",
                 summ.get("n_positions", 0))
    except Exception as exc:
        log.warning("dashboard: paper regen failed: %s", exc)


# ── 자산 관리 대시보드 (뱅크샐러드 export → portfolio.html) — 2026-06-04 ──
# bot/portfolio.py 가 저장한 모델(증권사별·자산배분·순자산·보유종목)을 읽어
# portfolio.html 렌더. 텔레그램 ZIP 업로드 핸들러가 ingest 후 호출(+startup/
# 자정 regen). _SCREENER_CSS(테마 vars --text/--muted/--pos/--neg/--border/
# --card/--bg) 재사용 + 도넛만 _PF_CSS 추가. 닫는 태그는 기존 페이지와 동일
# (</div> 까지; 브라우저 tolerant).
_PF_DONUT_COLORS = ["#4c9aff", "#3ec46d", "#f5a623", "#e2574c", "#b07cff",
                    "#22d3ee", "#f78fb3", "#facc15", "#2dd4bf", "#fb923c", "#a0aec0"]
_PF_CSS = """<style>
.nav a,.nav b{white-space:nowrap}
.pf-grid{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-start;margin:14px 0}
.pf-grid.pf-eqh{align-items:stretch}
.pf-grid.pf-eqh>.pf-card{display:flex;flex-direction:column}
.pf-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin:14px 0}
.pf-h{margin:0 0 10px;font-size:15px;font-weight:700;color:var(--text)}
.pf-donut{width:170px;height:170px;border-radius:50%}
.pf-hole{width:170px;height:170px;border-radius:50%;position:relative;flex:0 0 auto}
.pf-hole::after{content:"";position:absolute;inset:32px;border-radius:50%;background:var(--bg)}
.pf-leg{font-size:13px;margin:3px 0;color:var(--text)}
.pf-dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
.pf-tbl{width:100%;border-collapse:collapse;font-size:13px}
.pf-tbl th,.pf-tbl td{padding:6px 9px;border-bottom:1px solid var(--border);text-align:left;color:var(--text)}
.pf-tbl th{color:var(--muted);font-weight:600}
.pf-tbl td.r,.pf-tbl th.r{text-align:right;font-variant-numeric:tabular-nums}
.pf-tbl td.cen,.pf-tbl th.cen{text-align:center;font-variant-numeric:tabular-nums}
.pf-scroll{max-height:560px;overflow:auto;border:1px solid var(--border);border-radius:8px}
.pf-lnk{color:inherit;text-decoration:none}
.pf-lnk:hover{text-decoration:underline}
.pf-ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 10px}
.pf-ctl input[type=text],.pf-ctl select{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 9px;font-size:13px}
.pf-ctl label{font-size:13px;color:var(--muted);display:inline-flex;align-items:center;gap:4px;cursor:pointer}
.pf-mkt-filter .mf-btn{background:var(--bg);color:var(--muted);border:1px solid var(--border);border-radius:12px;padding:3px 10px;font-size:12px;cursor:pointer;white-space:nowrap}
.pf-mkt-filter .mf-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.pf-title-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.pf-title-row h1{margin:0}
.pf-send{background:var(--accent);color:#fff;border:0;border-radius:4px;padding:3px 7px;font-size:11px;cursor:pointer}
.pf-send:hover{opacity:.88}
.pf-send:disabled{opacity:.5;cursor:default}
.pf-tbl thead th{position:sticky;top:0;background:var(--card);z-index:1}
.pf-tbl th[data-k]{cursor:pointer;user-select:none;white-space:nowrap}
.pf-tbl th[data-k]::after{content:"↕";opacity:.3;font-size:9px;margin-left:3px}
.pf-tbl th[data-k]:hover{color:var(--text)}
.pf-tbl th[data-k]:hover::after{opacity:.65}
.pf-tbl th.pf-sorted::after{content:""}
.pf-ar{opacity:.85;font-size:10px;margin-left:2px}
@media (max-width:760px){.pf-grid>.pf-card{flex:1 1 100% !important;min-width:0 !important}}
</style>"""

# 보유 테이블 정렬/필터 JS (사용자 요청 2026-06-04). 의존성 0·IIFE 스코프. 데이터는
# 각 <tr> 의 data-* (raw 숫자/문자) 에 담겨 정렬·검색이 만/억 포맷 텍스트에 흔들리지
# 않는다. 헤더 클릭=정렬(숫자 desc·문자 asc 시작, 재클릭 토글), 검색·증권사·NOAH 필터.
_PF_TABLE_JS = """<script>(function(){
var tbl=document.getElementById('pf-tbl'); if(!tbl||!tbl.tBodies[0]) return;
var tb=tbl.tBodies[0], rows=[].slice.call(tb.rows);
var q=document.getElementById('pf-q'), bsel=document.getElementById('pf-broker'),
    nck=document.getElementById('pf-noah'), cnt=document.getElementById('pf-cnt');
var sortK=null, sortDir=1, mktFilter='ALL';
var mfBtns=[].slice.call(document.querySelectorAll('.pf-mkt-filter .mf-btn'));
mfBtns.forEach(function(btn){
  btn.addEventListener('click',function(){
    mktFilter=btn.dataset.mf;
    mfBtns.forEach(function(b){b.classList.toggle('active',b===btn);});
    apply();
  });
});
function num(v){var n=parseFloat(v); return isNaN(n)?null:n;}
function apply(){
 var qv=(q.value||'').trim().toLowerCase(), bv=bsel.value, nv=nck.checked, shown=0;
 rows.forEach(function(tr){
  var ok=true;
  if(mktFilter!=='ALL') ok=(tr.dataset.market===mktFilter);
  if(ok&&qv) ok=((tr.dataset.name||'').indexOf(qv)>=0)||((tr.dataset.tkr||'').indexOf(qv)>=0);
  if(ok&&bv) ok=(tr.dataset.broker===bv);
  if(ok&&nv) ok=!!(tr.dataset.noah);
  tr.style.display=ok?'':'none'; if(ok)shown++;
 });
 if(cnt) cnt.textContent=shown+'개 표시';
}
function sortBy(k,t){
 if(sortK===k){sortDir=-sortDir;}else{sortK=k;sortDir=(t==='n'?-1:1);}
 rows.slice().sort(function(a,b){
  var av=a.dataset[k], bv=b.dataset[k];
  if(t==='n'){var an=num(av),bn=num(bv);
   if(an===null&&bn===null)return 0; if(an===null)return 1; if(bn===null)return -1;
   return (an-bn)*sortDir;}
  return String(av).localeCompare(String(bv),'ko')*sortDir;
 }).forEach(function(tr){tb.appendChild(tr);});
 [].forEach.call(tbl.tHead.rows[0].cells,function(th){
  var ar=th.querySelector('.pf-ar'); if(ar)ar.remove();
  th.classList.remove('pf-sorted');
  if(th.dataset.k===k){th.classList.add('pf-sorted');
   var s=document.createElement('span'); s.className='pf-ar';
   s.textContent=(sortDir>0?' \\u25B2':' \\u25BC'); th.appendChild(s);}
 });
}
[].forEach.call(tbl.tHead.rows[0].cells,function(th){
 if(th.dataset.k) th.addEventListener('click',function(){sortBy(th.dataset.k,th.dataset.t);});
});
if(q)q.addEventListener('input',apply); if(bsel)bsel.addEventListener('change',apply);
if(nck)nck.addEventListener('change',apply);
apply();
})();</script>"""


# 자산 스냅샷 '보내기' — 렌더된 표(요약+보유종목, 손익변동·NOAH판정 포함)에서
# CSV 를 만들어 api/portfolio_send 로 POST → 서버가 본인 DM(텔레그램)/메일로
# 전송(사용자 요청 2026-06-06). raw 숫자(data-*) 사용 → Excel 에서 계산 가능.
_PF_SEND_JS = """<script>(function(){
  function esc(v){v=(v==null?'':String(v));return '"'+v.replace(/"/g,'""')+'"';}
  function snapDate(){var e=document.getElementById('pf-snap-date');return e?e.textContent.trim():'snapshot';}
  function tblRows(id){var t=document.getElementById(id);if(!t)return[];
    var rs=[];t.querySelectorAll('tr').forEach(function(tr){
      var a=[];for(var i=0;i<tr.cells.length;i++)a.push(tr.cells[i].textContent.trim());rs.push(a);});return rs;}
  function buildCsv(){
    var L=[];function row(a){L.push(a.map(esc).join(','));}
    row(['NOAH 자산 스냅샷', snapDate()]);row([]);
    row(['[요약]']);
    document.querySelectorAll('.stats .stat').forEach(function(s){
      var l=(s.querySelector('.stat-lbl')||{}).textContent||'';
      var n=(s.querySelector('.stat-num')||{}).textContent||'';
      row([l.trim(), n.trim()]);
    });
    var bro=tblRows('pf-bro');
    if(bro.length){row([]);row(['[증권사별]']);bro.forEach(function(r){row(r);});}
    var mov=tblRows('pf-movers');
    if(mov.length){row([]);row(['[수익률 TOP / WORST]']);mov.forEach(function(r){row(r);});}
    row([]);row(['[보유 종목]']);
    var hasChg=!!document.querySelector('#pf-tbl th[data-k="chg"]');
    var h=['종목','티커','증권사','평가금액','수익률(%)','평가손익'];
    if(hasChg)h.push('손익변동');h.push('NOAH판정');row(h);
    document.querySelectorAll('#pf-tbl tbody tr[data-name]').forEach(function(tr){
      var c=tr.cells;
      var a=[c[0].textContent.trim(),c[1].textContent.trim(),c[2].textContent.trim(),
             tr.getAttribute('data-eval')||'',tr.getAttribute('data-ret')||'',
             tr.getAttribute('data-pnl')||''];
      if(hasChg)a.push(tr.getAttribute('data-chg')||'');
      a.push((tr.getAttribute('data-noah')||'').trim());
      row(a);
    });
    var lo=tblRows('pf-loans');
    if(lo.length){row([]);row(['[대출]']);lo.forEach(function(r){row(r);});}
    var ins=tblRows('pf-ins');
    if(ins.length){row([]);row(['[보험]']);ins.forEach(function(r){row(r);});}
    return L.join('\\r\\n');
  }
  function send(to,btn){
    var t0=btn.textContent;btn.disabled=true;btn.textContent='전송 중…';
    fetch('api/portfolio_send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({to:to,csv:buildCsv(),date:snapDate()})})
      .then(function(r){return r.json();})
      .then(function(j){alert((j&&j.msg)||(j&&j.ok?'전송 완료':'전송 실패'));})
      .catch(function(){alert('전송 실패 (네트워크)');})
      .finally(function(){btn.disabled=false;btn.textContent=t0;});
  }
  var tg=document.getElementById('pf-send-tg');if(tg)tg.onclick=function(){send('telegram',tg);};
  var em=document.getElementById('pf-send-em');if(em)em.onclick=function(){send('email',em);};
})();</script>"""


def _pf_won(v) -> str:
    """₩ 금액 → 억/만 약식. None→'-'."""
    if v is None:
        return "-"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    neg = n < 0
    n = abs(n)
    if n >= 1e8:
        s = f"{n / 1e8:.1f}억"
    elif n >= 1e4:
        s = f"{n / 1e4:,.0f}만"
    else:
        s = f"{n:,.0f}"
    return ("-" if neg else "") + s + "원"


def _pf_col(v) -> str:
    return "var(--pos)" if (v or 0) >= 0 else "var(--neg)"


def _pf_rating_col(rating) -> str:
    """NOAH 판정 → 방향 색. 매도/Sell 우선 검사(적극매도 오분류 방지) → 매수 → 그 외 muted.
    무의미한 파란 링크 대신 판정 방향을 한눈에(사용자 '파란색 말고 똑같이' 후속)."""
    s = str(rating or "").lower()
    if any(k in s for k in ("매도", "sell", "underweight")):
        return "var(--neg)"
    if any(k in s for k in ("매수", "buy", "overweight")):
        return "var(--pos)"
    return "var(--muted)"


def _noah_lookup(noah: dict, tkr) -> tuple:
    """보유 티커 → (NOAH info, 아카이브 full 티커) 또는 (None, None).
    KR 은 DART 6자리 코드(suffix 없음)라 .KS/.KQ 둘 다 시도해 분석 아카이브
    ('039030.KS')와 매칭. 이미 suffix 가 붙은 경우(.KS→.KQ 또는 반대)도 처리.
    해외(LRCX 등)는 그대로 매칭."""
    if not tkr or not noah:
        return (None, None)
    tkr_str = str(tkr)
    if tkr_str in noah:
        return (noah[tkr_str], tkr_str)
    base = tkr_str
    for sfx in (".KS", ".KQ"):
        if tkr_str.endswith(sfx):
            base = tkr_str[:-len(sfx)]
            break
    for cand in (f"{base}.KS", f"{base}.KQ"):
        if cand in noah:
            return (noah[cand], cand)
    return (None, None)


def _render_portfolio_page(model, noah=None) -> str:
    """portfolio.json 모델 → portfolio.html. 추천 구성: 순자산 헤더 → 자산배분
    도넛 → 증권사별 + 수익률 TOP/WORST → 보유 종목 테이블 → 대출/보험.

    noah = {ticker: {rating, ret, date}} (선택) — 있으면 보유 테이블에 NOAH 최근
    판정 + 5거래일 성과 오버레이(더리치/도미노에 없는 우리만의 강점, 증분5)."""
    import html as _html
    # 풀 nav — 메인(NOAH 아카이브)을 맨 앞, 나머지는 메인 index 와 동일 순서,
    # 자산(현재 페이지)은 굵게(비링크). 사용자 정책 2026-06-04: 자산 대시보드를
    # 허브로 쓰되 기존 메인을 첫 링크로.
    nav = (
        '<div class="nav">'
        '<a href="budget.html">📒 가계부</a>'
        ' · <a href="index.html">🦉 NOAH 종목분석</a>'
        '</div>')
    if not model or not model.get("holdings"):
        return _SCREENER_CSS + _PF_CSS + (
            '<div class="wrap">' + nav + '<h1>💼 자산 관리</h1>'
            '<p class="sub">아직 업로드된 자산이 없습니다. 텔레그램 봇에 '
            '<b>뱅크샐러드 자산 export (zip)</b> 를 보내면 자동으로 여기 표시됩니다.</p></div>')

    nw = model.get("net_worth", {})
    holdings = model.get("holdings", [])
    eval_sum = sum(h.get("평가금액") or 0 for h in holdings)
    cost_sum = sum(h.get("투자원금") or 0 for h in holdings)
    _alloc = model.get("asset_allocation", {})
    cash_in_invest = model.get("cash_in_invest") or 0
    # 옛 모델(재분류 전) 호환: asset_allocation 이 아직 원본이면 렌더 시 재분류
    if cash_in_invest > 0 and _alloc.get("투자성 자산", 0) > eval_sum:
        _alloc["투자성 자산"] = _alloc["투자성 자산"] - cash_in_invest
        _alloc["자유입출금 자산"] = _alloc.get("자유입출금 자산", 0) + cash_in_invest
    invest_total = eval_sum
    pnl = eval_sum - cost_sum
    pnl_pct = (pnl / cost_sum * 100) if cost_sum else 0.0
    # '보유 종목' 카운트 = 고유 종목(증권사 중복 제외). 저장된 모델(이 기능
    # 이전 업로드)은 distinct_count 가 없으므로 **렌더 시점에 holdings 로 재계산**
    # → 재업로드 없이 다음 대시보드 regen 에 즉시 반영. 행 수(포지션 건수)는
    # _positions 로 별도 유지(증권사별 행·필터 보존).
    try:
        from bot.portfolio import _distinct_stock_keys
        _distinct = len(_distinct_stock_keys(holdings))
    except Exception:
        _distinct = model.get("distinct_count", len(holdings))
    _positions = len(holdings)
    as_of = _html.escape(str(model.get("as_of") or ""))
    # 수동 업로드(뱅크샐러드 RAG 채널)라 '마지막 업데이트' 시각 명시 (사용자
    # 요청 2026-06-04). ingest 가 save() 에 박은 _saved_ts(epoch) → KST.
    import datetime as _dt
    _ts = model.get("_saved_ts")
    if _ts:
        updated = _dt.datetime.fromtimestamp(
            _ts, _dt.timezone(_dt.timedelta(hours=9))
        ).strftime("%Y-%m-%d %H:%M")
    else:
        updated = "—"
    # 보내기(CSV) 파일명·헤더용 스냅샷 날짜.
    _snap_date = (_dt.datetime.fromtimestamp(
        _ts, _dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d")
        if _ts else "snapshot")
    prev = model.get("prev")
    delta_html = ""
    _prev_pnl_map: dict[str, float] = {}
    _show_chg = False
    _chg_dates = ""
    if isinstance(prev, dict):
        _pts = prev.get("_saved_ts")
        _prev_pnl_map = prev.get("holdings_pnl") or {}
        _show_chg = bool(_prev_pnl_map)
        pdate = (_dt.datetime.fromtimestamp(
            _pts, _dt.timezone(_dt.timedelta(hours=9))).strftime("%m-%d %H:%M")
            if _pts else str(prev.get("as_of") or "이전"))
        _cdate = (_dt.datetime.fromtimestamp(
            _ts, _dt.timezone(_dt.timedelta(hours=9))).strftime("%m-%d %H:%M")
            if _ts else "현재")
        _chg_dates = f"{pdate} → {_cdate}"

        def _dlt(cur, was):
            cur = cur or 0
            was = was or 0
            d = cur - was
            p = (d / was * 100) if was else 0.0
            sign = "+" if d >= 0 else "−"
            col = "var(--pos)" if d >= 0 else "var(--neg)"
            return (f'<span style="color:{col}">{sign}{_pf_won(abs(d))} '
                    f'({sign}{abs(p):.1f}%)</span>')
        prev_eval = prev.get("주식평가", prev.get("투자성 자산"))
        fund_now = model.get("fund_in_pension") or 0
        fund_was = prev.get("연금펀드") or 0
        fund_part = (f' · 연금펀드 {_dlt(fund_now, fund_was)}'
                     if fund_now or fund_was else "")
        delta_html = (
            '<div style="margin-top:10px;font-size:12px;color:var(--muted);'
            'border-top:1px solid var(--border);padding-top:8px">'
            f'📈 지난 업데이트({pdate}) 대비 — 순자산 '
            f'{_dlt(nw.get("순자산"), prev.get("순자산"))} · 주식평가 '
            f'{_dlt(eval_sum, prev_eval)}{fund_part}</div>')

    # 증분(자산 변화)을 주식 요약 패널 바로 밑에 배치(사용자 2026-06-04). prev 가
    # 없으면(첫 업로드·같은 날짜만 업로드) '사라진 게 아니라 비교 대상 대기' 안내.
    if delta_html:
        delta_section = delta_html
    else:
        delta_section = (
            '<div style="margin-top:10px;font-size:12px;color:var(--muted);'
            'border-top:1px solid var(--border);padding-top:8px">'
            '📈 자산 변화 — export 가 2회 이상 쌓이면 여기에 순자산·투자자산 '
            '증분 표시</div>')

    cash_stat = (f'<div class="stat"><div class="stat-num">{_pf_won(cash_in_invest)}</div>'
                  '<div class="stat-lbl">예수금 (자유입출금)</div></div>') if cash_in_invest else ""
    stats = (
        '<div class="stats">'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("순자산"))}</div><div class="stat-lbl">순자산</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("총자산"))}</div><div class="stat-lbl">총자산</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("총부채"))}</div><div class="stat-lbl">총부채</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(eval_sum)}</div><div class="stat-lbl">주식 평가</div></div>'
        + cash_stat +
        f'<div class="stat"><div class="stat-num" style="color:{_pf_col(pnl)}">{_pf_won(pnl)} ({pnl_pct:+.1f}%)</div><div class="stat-lbl">주식 평가손익</div></div>'
        f'<div class="stat"><div class="stat-num">{_distinct}</div><div class="stat-lbl">보유 종목</div></div>'
        '</div>')

    # 자산 배분 도넛
    alloc_items = sorted(model.get("asset_allocation", {}).items(), key=lambda kv: -kv[1])
    tot = sum(a for _, a in alloc_items) or 1
    stops, legend, acc = [], [], 0.0
    for i, (cat, amt) in enumerate(alloc_items):
        pct = amt / tot * 100
        col = _PF_DONUT_COLORS[i % len(_PF_DONUT_COLORS)]
        stops.append(f"{col} {acc:.2f}% {acc + pct:.2f}%")
        # 카테고리 표시명 보정
        if cat == "동산":
            disp = "동산 (자동차)"
        elif cat == "자유입출금 자산" and cash_in_invest > 0:
            disp = "자유입출금 자산 (예수금 포함)"
        else:
            disp = cat
        legend.append(f'<div class="pf-leg"><span class="pf-dot" style="background:{col}"></span>'
                      f'{_html.escape(disp)} <b>{_pf_won(amt)}</b> '
                      f'<span style="color:var(--muted)">({pct:.1f}%)</span></div>')
        acc += pct
    # 주식 요약 패널 — 도넛 카드 우측 빈 공간 활용 (배분 도넛과 중복 안 되게
    # equity sleeve 성과 위주: 평가/원금/손익/비중/승률).
    win = sum(1 for h in holdings if (h.get("수익률") or 0) > 0)
    loss = sum(1 for h in holdings if (h.get("수익률") or 0) < 0)
    rated_n = win + loss
    winrate = (win / rated_n * 100) if rated_n else 0.0
    stock_wt = (eval_sum / nw["총자산"] * 100) if nw.get("총자산") else 0.0
    equity_panel = (
        '<div style="flex:1 1 200px;min-width:190px">'
        '<div class="pf-h" style="font-size:13px;color:var(--muted)">💹 주식 요약</div>'
        f'<div class="pf-leg">평가금액 <b>{_pf_won(eval_sum)}</b></div>'
        f'<div class="pf-leg">투자원금 <b>{_pf_won(cost_sum)}</b></div>'
        f'<div class="pf-leg">평가손익 <b style="color:{_pf_col(pnl)}">{_pf_won(pnl)} ({pnl_pct:+.1f}%)</b></div>'
        f'<div class="pf-leg">총자산 대비 주식 <b>{stock_wt:.1f}%</b></div>'
        f'<div class="pf-leg">수익 {win} · 손실 {loss} · 승률 <b>{winrate:.0f}%</b></div>'
        + delta_section + '</div>')
    donut_block = ""
    if stops:
        donut_block = (
            '<div class="pf-card"><div class="pf-h">자산 배분</div><div class="pf-grid">'
            f'<div class="pf-hole"><div class="pf-donut" style="background:conic-gradient({",".join(stops)})"></div></div>'
            f'<div style="flex:1 1 240px;min-width:200px">{"".join(legend)}</div>'
            + equity_panel + '</div></div>')

    # 증권사별
    bro_rows = ""
    for b, d in sorted(model.get("by_broker", {}).items(), key=lambda kv: -kv[1]["평가금액"]):
        bro_rows += (f'<tr><td>{_html.escape(b)}</td><td class="r">{_pf_won(d["평가금액"])}</td>'
                     f'<td class="r">{d["종목수"]}</td>'
                     f'<td class="r" style="color:{_pf_col(d["평가손익"])}">{_pf_won(d["평가손익"])}</td></tr>')
    overseas = sum(h.get("평가금액") or 0 for h in holdings if _holding_market(h) != "KR")
    domestic = max(eval_sum - overseas, 0)
    eq_tot = eval_sum or 1
    nw_bar = (
        '<div style="margin-top:14px">'
        '<div style="font-size:12px;color:var(--muted);margin-bottom:5px">주식 국내 / 해외</div>'
        '<div style="display:flex;height:14px;border-radius:7px;overflow:hidden;background:var(--border)">'
        f'<div title="국내" style="background:#4c9aff;width:{domestic / eq_tot * 100:.1f}%"></div>'
        f'<div title="해외" style="background:#f5a623;width:{overseas / eq_tot * 100:.1f}%"></div>'
        '</div>'
        '<div style="font-size:12px;color:var(--muted);margin-top:5px">'
        f'<span style="color:#4c9aff">●</span> 국내 {_pf_won(domestic)} ({domestic / eq_tot * 100:.0f}%) · '
        f'<span style="color:#f5a623">●</span> 해외 {_pf_won(overseas)} ({overseas / eq_tot * 100:.0f}%)</div>'
        '</div>')
    # 수익률 분포 — 증권사별 카드를 TOP/WORST 와 높이 맞추는 유용한 채움(겹침 없음,
    # '다른 괜찮은거'). 종목 수 기준 4구간 stacked bar + 카운트.
    _rs = [h.get("수익률") for h in holdings if h.get("수익률") is not None]
    _n = len(_rs) or 1
    _bw = sum(1 for r in _rs if r >= 100)
    _w = sum(1 for r in _rs if 0 <= r < 100)
    _sl = sum(1 for r in _rs if -50 <= r < 0)
    _bl = sum(1 for r in _rs if r < -50)
    dist_html = (
        '<div style="margin-top:12px">'
        '<div style="font-size:12px;color:var(--muted);margin-bottom:5px">수익률 분포 (종목 수)</div>'
        '<div style="display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--border)">'
        f'<div title="+100%↑" style="background:#2ca44b;width:{_bw / _n * 100:.1f}%"></div>'
        f'<div title="0~+100%" style="background:#7bd389;width:{_w / _n * 100:.1f}%"></div>'
        f'<div title="-50~0%" style="background:#f0a35e;width:{_sl / _n * 100:.1f}%"></div>'
        f'<div title="-50%↓" style="background:#e2574c;width:{_bl / _n * 100:.1f}%"></div>'
        '</div>'
        '<div style="font-size:11px;color:var(--muted);margin-top:5px">'
        f'대박(+100%↑) {_bw} · 수익 {_w} · 손실 {_sl} · 큰손실(-50%↓) {_bl}</div>'
        '</div>')
    bro_block = ('<div class="pf-card" style="flex:1;min-width:300px"><div class="pf-h">증권사별</div>'
                 '<table class="pf-tbl" id="pf-bro"><tr><th>증권사</th><th class="r">평가금액</th><th class="r">종목</th><th class="r">손익</th></tr>'
                 + bro_rows + '</table>' + nw_bar + dist_html + '</div>')

    # 수익률 TOP / WORST
    def _mv(items):
        out = ""
        for h in items:
            r = h.get("수익률")
            out += (f'<tr><td>{_html.escape(h.get("상품명") or "")}</td>'
                    f'<td class="r" style="color:{_pf_col(r)}">{r:+.1f}%</td>'
                    f'<td class="r">{_pf_won(h.get("평가금액"))}</td></tr>')
        return out
    movers_block = ('<div class="pf-card" style="flex:1;min-width:300px"><div class="pf-h">수익률 TOP / WORST</div>'
                    '<table class="pf-tbl" id="pf-movers"><tr><th>종목</th><th class="r">수익률</th><th class="r">평가금액</th></tr>'
                    + _mv(model.get("top_gainers", [])) + _mv(model.get("top_losers", [])) + '</table></div>')

    # 보유 종목 전체 + NOAH 판정 오버레이(증분5) + 정렬/필터(2026-06-04 사용자 요청).
    # 각 행에 data-*(raw 숫자/문자) 부착 → JS 정렬·검색이 만/억 포맷에 안 흔들림.
    noah = noah or {}
    noah_n = 0
    hl_rows = ""
    _holds_sorted = sorted(holdings, key=lambda x: -(x.get("평가금액") or 0))
    for h in _holds_sorted:
        r = h.get("수익률")
        rtxt = f'{r:+.1f}%' if r is not None else "—"
        nm = _html.escape(h.get("상품명") or "")
        tkr = h.get("ticker")
        broker = _html.escape(h.get("금융사") or "")
        ev, pnl = h.get("평가금액"), h.get("평가손익")
        # NOAH 최근 판정 (분석 기록 있는 종목만). KR 6자리 코드는 .KS/.KQ 둘 다
        # 시도해 아카이브와 매칭. 분석 있을 때만 링크(404 방지).
        ninfo, full_tkr = _noah_lookup(noah, tkr)
        # 종목명: 분석 있으면 링크하되 일반 텍스트색(pf-lnk) — 사용자 '파란색 말고 똑같이'.
        # 상세 페이지는 {date}/{ticker}.html 구조 (ticker_*.html 아님).
        _noah_date = (ninfo or {}).get("date", "")
        _noah_href = (f'{_html.escape(_noah_date)}/{_html.escape(full_tkr)}.html'
                      if full_tkr and _noah_date else "")
        nm_cell = (f'<a class="pf-lnk" href="{_noah_href}">{nm}</a>'
                   if _noah_href else nm)
        noah_rating = ""
        if ninfo:
            noah_n += 1
            noah_rating = str(ninfo.get("rating") or "")
            _rr = ninfo.get("ret")
            _rrt = (f' <span style="color:{_pf_col(_rr)}">{_rr:+.1f}%</span>'
                    if _rr is not None else "")
            # 판정 = 방향 색(매수 양/매도 음/보유 muted), 링크는 pf-lnk(밑줄 hover만).
            noah_cell = (f'<a class="pf-lnk" href="{_noah_href}" '
                         f'style="color:{_pf_rating_col(noah_rating)};font-weight:600">'
                         f'{_html.escape(noah_rating)}</a>{_rrt}')
        else:
            noah_cell = '<span style="color:var(--muted)">—</span>'
        # 손익변동 컬럼 — baseline 의 holdings_pnl 대비 현재 평가손익 차이.
        _chg_val = None
        _chg_cell = ""
        if _show_chg:
            _pkey = f"{h.get('상품명', '')}|{h.get('금융사', '')}"
            _prev_pnl = _prev_pnl_map.get(_pkey)
            if _prev_pnl is not None:
                _chg_val = (pnl or 0) - _prev_pnl
                if _chg_val == 0:
                    _chg_cell = '<td class="cen" style="color:var(--muted)">—</td>'
                else:
                    _csign = "+" if _chg_val > 0 else ""
                    _chg_cell = (f'<td class="cen" style="color:{_pf_col(_chg_val)}">'
                                 f'{_csign}{_pf_won(_chg_val)}</td>')
            else:
                _chg_cell = '<td class="cen"><span style="font-size:11px;color:var(--accent)">신규</span></td>'
        _hmkt = _holding_market(h)
        _da = (f'data-name="{nm.lower()}" data-tkr="{_html.escape(str(tkr or "")).lower()}" '
               f'data-broker="{broker}" data-eval="{ev if ev is not None else ""}" '
               f'data-ret="{r if r is not None else ""}" '
               f'data-pnl="{pnl if pnl is not None else ""}" '
               f'data-chg="{_chg_val if _chg_val is not None else ""}" '
               f'data-noah="{_html.escape(noah_rating)}" '
               f'data-market="{_hmkt}"')
        hl_rows += (f'<tr {_da}><td>{nm_cell}</td><td>{_html.escape(str(tkr or "—"))}</td>'
                    f'<td>{broker}</td><td class="r">{_pf_won(ev)}</td>'
                    f'<td class="r" style="color:{_pf_col(r)}">{rtxt}</td>'
                    f'<td class="r" style="color:{_pf_col(pnl)}">{_pf_won(pnl)}</td>'
                    + _chg_cell +
                    f'<td>{noah_cell}</td></tr>')
    # 증권사 드롭다운(평가금액 큰 순 등장 순서)
    _brokers: list[str] = []
    for h in _holds_sorted:
        b = h.get("금융사") or ""
        if b and b not in _brokers:
            _brokers.append(b)
    _opts = '<option value="">전체 증권사</option>' + ''.join(
        f'<option value="{_html.escape(b)}">{_html.escape(b)}</option>' for b in _brokers)
    # 국가별 필터 버튼
    _hmkt_counts: dict[str, int] = {}
    for h in _holds_sorted:
        _mk = _holding_market(h)
        _hmkt_counts[_mk] = _hmkt_counts.get(_mk, 0) + 1
    _mkt_order = ["KR", "US", "JP", "TW", "CN", "HK"]
    _mkt_btns = '<button class="mf-btn active" data-mf="ALL">전체</button>'
    for _mk in _mkt_order:
        if _mk in _hmkt_counts:
            _mkt_btns += f'<button class="mf-btn" data-mf="{_mk}">{_mk} {_hmkt_counts[_mk]}</button>'
    _mkt_filter = f'<div class="pf-mkt-filter" style="display:flex;gap:5px;flex-wrap:wrap">{_mkt_btns}</div>'
    _ctl = ('<div class="pf-ctl"><input type="text" id="pf-q" placeholder="🔎 종목·티커 검색">'
            f'<select id="pf-broker">{_opts}</select>'
            '<label><input type="checkbox" id="pf-noah"> NOAH 분석만</label>'
            f'{_mkt_filter}'
            '<span id="pf-cnt" style="font-size:12px;color:var(--muted)"></span></div>'
            ) if holdings else ''
    _send_btns = (
        '<button id="pf-send-tg" class="pf-send" type="button">📤 텔레그램 보내기</button>'
        '<button id="pf-send-em" class="pf-send" type="button">📧 이메일 보내기</button>'
        f'<span id="pf-snap-date" hidden>{_snap_date}</span>'
    ) if holdings else ''
    _chg_th = (
        '<th class="cen" data-k="chg" data-t="n">손익변동'
        f'<br><span style="font-weight:400;font-size:10px;color:var(--muted)">'
        f'{_html.escape(_chg_dates)}</span></th>'
    ) if _show_chg else ''
    _head = ('<thead><tr><th data-k="name" data-t="s">종목</th>'
             '<th data-k="tkr" data-t="s">티커</th>'
             '<th data-k="broker" data-t="s">증권사</th>'
             '<th class="r" data-k="eval" data-t="n">평가금액</th>'
             '<th class="r" data-k="ret" data-t="n">수익률</th>'
             '<th class="r" data-k="pnl" data-t="n">평가손익</th>'
             + _chg_th +
             '<th data-k="noah" data-t="s">NOAH 판정</th></tr></thead>')
    # 헤더 카운트 = 고유 종목 수(증권사 중복 제외, 위에서 렌더 시점 재계산).
    # 행은 증권사별 포지션을 유지하므로(증권사 필터 보존) 건수와 다르면 병기.
    _hdr_cnt = (f'{_distinct}종목 · {_positions}건'
                if _positions != _distinct else f'{_distinct}종목')
    holdings_block = ('<div class="pf-card"><div class="pf-h">보유 종목 (' + _hdr_cnt
                      + (f' · NOAH 분석 {noah_n}' if noah_n else '') + ')</div>' + _ctl
                      + '<div class="pf-scroll"><table class="pf-tbl" id="pf-tbl">'
                      + _head + '<tbody>' + hl_rows + '</tbody></table></div>'
                      + _PF_TABLE_JS + _PF_SEND_JS + '</div>')

    # 대출 · 보험 — 대출은 한도(원금)+잔액+금리(전부 노출), 보험은 표로
    # (사용자 요청 2026-06-04: 보험도 제대로, 대출 다 반영).
    loans, ins = model.get("loans", []), model.get("insurance", [])
    extra = ""
    if loans or ins:
        extra = '<div class="pf-card"><div class="pf-h">대출 · 보험</div>'
        if loans:
            extra += ('<div style="font-size:13px;color:var(--muted);margin:2px 0 4px">대출 ('
                      + str(len(loans)) + ')</div>'
                      '<table class="pf-tbl" id="pf-loans"><tr><th>대출</th><th>금융사</th>'
                      '<th class="r">한도(원금)</th><th class="r">잔액</th><th class="r">금리</th></tr>')
            for l in loans:
                gr = f'{l.get("대출금리")}%' if l.get("대출금리") is not None else "—"
                extra += (f'<tr><td>{_html.escape(l.get("상품명") or l.get("대출종류") or "")}</td>'
                          f'<td>{_html.escape(l.get("금융사") or "")}</td>'
                          f'<td class="r">{_pf_won(l.get("대출원금"))}</td>'
                          f'<td class="r">{_pf_won(l.get("대출잔액"))}</td>'
                          f'<td class="r">{gr}</td></tr>')
            extra += '</table>'
        if ins:
            extra += ('<div style="font-size:13px;color:var(--muted);margin:12px 0 4px">보험 ('
                      + str(len(ins)) + ')</div>'
                      '<table class="pf-tbl" id="pf-ins"><tr><th>보험사</th><th>보험명</th>'
                      '<th>상태</th><th class="r">총납입</th></tr>')
            for i in ins:
                extra += (f'<tr><td>{_html.escape(i.get("금융사") or "")}</td>'
                          f'<td>{_html.escape(i.get("보험명") or "")}</td>'
                          f'<td>{_html.escape(str(i.get("계약상태") or ""))}</td>'
                          f'<td class="r">{_pf_won(i.get("총납입금"))}</td></tr>')
            extra += '</table>'
        extra += '</div>'

    note = ('<p class="sub" style="margin-top:14px;color:var(--muted);font-size:12px">'
            '뱅크샐러드 export 스냅샷 기준 — 평가금액·수익률은 export 시점 값. '
            "'보유 종목' 수는 증권사 중복 제외 고유 종목 기준"
            '(같은 종목을 여러 증권사에 보유해도 1종목). 표는 증권사별 포지션을 '
            '그대로 보여줘 건수(M건)는 종목 수보다 많을 수 있음. '
            "'손익변동'은 직전 다른 날짜 업로드 대비 종목별 평가손익 증감"
            '(벌거나 잃은 금액 · 첫 업로드·같은 날 재업로드는 미표시). '
            '표 헤더 클릭 정렬 · 검색/증권사/NOAH 필터 가능. '
            '티커 매칭 종목은 종목명 클릭 시 NOAH 분석으로 연결(분석 기록 있을 때). '
            '📤 텔레그램·📧 이메일 버튼으로 이 스냅샷(요약+보유종목)을 CSV로 전송(모바일용). '
            f'기준 기간: {as_of}</p>')

    return (_SCREENER_CSS + _PF_CSS + '<div class="wrap">' + nav
            + '<div class="pf-title-row"><h1>💼 자산 관리</h1>'
            + _send_btns + '</div>'
            + '<p class="sub">뱅크샐러드 전 계좌 통합 — 증권사·예적금·부동산·동산(자동차)·대출·보험</p>'
            + ('<p class="sub" style="color:var(--muted);font-size:13px;margin-top:2px">'
               f'🕒 마지막 업데이트 <b style="color:var(--text)">{updated}</b> '
               f'(수동 — 뱅크샐러드 RAG 채널 업로드) · 데이터 기간 {as_of}</p>')
            + stats + donut_block
            + '<div class="pf-grid pf-eqh">' + bro_block + movers_block + '</div>'
            + holdings_block + extra + note + '</div>')


def regenerate_portfolio_index() -> None:
    """portfolio.json → portfolio.html (ARCHIVE_ROOT). 텔레그램 ingest 후 +
    startup/자정 regen 에서 호출. 오류는 swallow."""
    try:
        from bot.portfolio import load as _pf_load, backfill_cash_in_invest
        try:
            backfill_cash_in_invest()
        except Exception:
            pass
        model = _pf_load()
        # 보유종목 ↔ 분석 아카이브 join — 종목별 최근 판정+5거래일 성과(증분5).
        # 차트 과거-추천 마커와 동일 헬퍼 재사용(_ticker_analysis_markers 의 마지막
        # = 최신). read-only (네트워크 0). 실패해도 대시보드는 정상(graceful).
        noah: dict[str, dict] = {}
        try:
            records = _load_all()
            resolved = _build_resolved_lookup()
            by_t: dict[str, list[dict]] = {}
            for r in records:
                by_t.setdefault(r.get("ticker", ""), []).append(r)
            for t, recs in by_t.items():
                if not t:
                    continue
                mks = _ticker_analysis_markers(recs, resolved)
                if mks:
                    last = mks[-1]
                    noah[t] = {"rating": last.get("rating"), "ret": last.get("ret"),
                               "date": last.get("time")}
        except Exception as exc:
            log.warning("portfolio NOAH overlay build failed: %s", exc)
        html = _render_portfolio_page(model, noah)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "portfolio.html").write_text(html, encoding="utf-8")
        log.info("dashboard: portfolio.html regenerated (NOAH overlay %d)", len(noah))
    except Exception as exc:
        log.warning("dashboard: portfolio regen failed: %s", exc)
    # 가계부(현금흐름)는 같은 export 산물이라 자산 regen 시 항상 함께 갱신.
    regenerate_budget_index()


# ── 가계부 대시보드 (뱅크샐러드 현금흐름 → budget.html) — 2026-06-04 P2 ──
# 자산(portfolio)과 별도 surface. 같은 export 의 '2.현금흐름현황' 을 모델링한
# budget.json 을 월별 수입/지출/순저축/저축률 + 카테고리 breakdown + 매트릭스로
# 렌더. 사용자 요청 "가계부는 또 다른 별도 대시보드".
_BUDGET_CSS = """<style>
.bg-chart{display:flex;gap:6px;align-items:flex-end;overflow-x:auto;padding:10px 2px 2px}
.bg-col{display:flex;flex-direction:column;align-items:center;gap:4px;flex:1 1 0;min-width:34px}
.bg-bars{display:flex;align-items:flex-end;gap:2px;height:140px}
.bg-bar{width:12px;border-radius:2px 2px 0 0;min-height:1px}
.bg-mo{font-size:10px;color:var(--muted);white-space:nowrap}
.bg-sr{font-size:10px;color:var(--muted)}
.bg-mtx{overflow:auto;max-height:480px;border:1px solid var(--border);border-radius:8px}
.bg-mtx td.r,.bg-mtx th.r{text-align:right;font-variant-numeric:tabular-nums}
.bg-mtx td,.bg-mtx th{white-space:nowrap}
.bg-mtx thead th{z-index:2}
.bg-mtx tbody td:first-child{position:sticky;left:0;background:var(--card);z-index:1}
.bg-mtx thead th:first-child{position:sticky;left:0;z-index:3}
</style>"""

_BUDGET_KIND = {"income": "수입", "expense": "지출", "total_income": "수입계",
                "total_expense": "지출계", "total": "계"}

_BG_SEND_JS = """<script>(function(){
  function esc(v){v=(v==null?'':String(v));return '"'+v.replace(/"/g,'""')+'"';}
  function tblRows(id){var t=document.getElementById(id);if(!t)return[];
    var rs=[];t.querySelectorAll('tr').forEach(function(tr){
      var a=[];for(var i=0;i<tr.cells.length;i++)a.push(tr.cells[i].textContent.trim());rs.push(a);});return rs;}
  function legItems(id){var c=document.getElementById(id);if(!c)return[];
    var rs=[];c.querySelectorAll('.pf-leg').forEach(function(d){
      var b=d.querySelector('b');var txt=d.textContent.trim();
      var nm=txt.split(/\\s+/)[0];var amt=b?b.textContent.trim():'';
      rs.push([nm,amt]);});return rs;}
  function buildCsv(){
    var L=[];function row(a){L.push(a.map(esc).join(','));}
    var dt=document.getElementById('bg-snap-date');
    var d=dt?dt.textContent.trim():'budget';
    row(['NOAH 가계부 스냅샷', d]);row([]);
    row(['[요약]']);
    document.querySelectorAll('.stats .stat').forEach(function(s){
      var l=(s.querySelector('.stat-lbl')||{}).textContent||'';
      var n=(s.querySelector('.stat-num')||{}).textContent||'';
      row([l.trim(), n.trim()]);
    });
    var ec=legItems('bg-exp-cats');
    if(ec.length){row([]);row(['[지출 카테고리]']);row(['항목','금액']);ec.forEach(function(r){row(r);});}
    var ic=legItems('bg-inc-cats');
    if(ic.length){row([]);row(['[수입 구성]']);row(['항목','금액']);ic.forEach(function(r){row(r);});}
    var mt=tblRows('bg-mtx-tbl');
    if(mt.length){row([]);row(['[현금흐름 상세]']);mt.forEach(function(r){row(r);});}
    return L.join('\\r\\n');
  }
  function send(to,btn){
    var t0=btn.textContent;btn.disabled=true;btn.textContent='전송 중…';
    var dt=document.getElementById('bg-snap-date');
    var d=dt?dt.textContent.trim():'budget';
    fetch('api/portfolio_send',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({to:to,csv:buildCsv(),date:d,kind:'budget'})})
      .then(function(r){return r.json();})
      .then(function(j){alert((j&&j.msg)||(j&&j.ok?'전송 완료':'전송 실패'));})
      .catch(function(){alert('전송 실패 (네트워크)');})
      .finally(function(){btn.disabled=false;btn.textContent=t0;});
  }
  var tg=document.getElementById('bg-send-tg');if(tg)tg.onclick=function(){send('telegram',tg);};
  var em=document.getElementById('bg-send-em');if(em)em.onclick=function(){send('email',em);};
})();</script>"""


def _budget_nav() -> str:
    """가계부 페이지 nav — 자산 + NOAH 종목분석만 (나머지는 NOAH 쪽에)."""
    return (
        '<div class="nav">'
        '<a href="portfolio.html">💼 자산</a>'
        ' · <a href="index.html">🦉 NOAH 종목분석</a>'
        '</div>')


def _render_budget_page(budget) -> str:
    """budget.json 모델 → budget.html. 요약 카드 → 월별 수입/지출 막대 →
    지출 카테고리 도넛 → 카테고리×월 매트릭스. 분류가 빗나가도 매트릭스는 원본
    그대로라 데이터 유실 0 (사용자가 보고 수정)."""
    import html as _html
    nav = _budget_nav()
    if not budget or not budget.get("months"):
        return _SCREENER_CSS + _PF_CSS + _BUDGET_CSS + (
            '<div class="wrap">' + nav + '<h1>📒 가계부</h1>'
            '<p class="sub">아직 현금흐름 데이터가 없습니다. 뱅크샐러드 export(zip)를 '
            'RAG 채널에 올리면 자산과 함께 가계부가 표시됩니다.</p></div>')
    months = budget["months"]
    income = budget.get("income", [])
    expense = budget.get("expense", [])
    net = budget.get("net", [])
    srate = budget.get("savings_rate", [])
    tot = budget.get("totals", {})
    n = len(months)

    # 업데이트 시각
    import datetime as _dt
    _ts = budget.get("_saved_ts")
    updated = (_dt.datetime.fromtimestamp(_ts, _dt.timezone(_dt.timedelta(hours=9)))
               .strftime("%Y-%m-%d %H:%M") if _ts else "—")

    def _pct(v):
        return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"

    # 요약 카드 (기간 월평균 + 저축률)
    cards = (
        '<div class="stats">'
        f'<div class="stat"><div class="stat-num" style="color:var(--pos)">{_pf_won(tot.get("income_avg"))}</div><div class="stat-lbl">월평균 수입</div></div>'
        f'<div class="stat"><div class="stat-num" style="color:var(--neg)">{_pf_won(tot.get("expense_avg"))}</div><div class="stat-lbl">월평균 지출</div></div>'
        f'<div class="stat"><div class="stat-num" style="color:{_pf_col(tot.get("net"))}">{_pf_won((tot.get("net") or 0) / n if n else 0)}</div><div class="stat-lbl">월평균 순저축</div></div>'
        f'<div class="stat"><div class="stat-num">{_pct(tot.get("savings_rate"))}</div><div class="stat-lbl">저축률(기간)</div></div>'
        '</div>')

    # 월별 수입/지출 막대 (수입 녹 · 지출 빨강), 저축률 라벨
    H = 140
    maxv = max([*(income or [0]), *(expense or [0]), 1])
    cols = ""
    for i, mo in enumerate(months):
        iv = income[i] if i < len(income) else 0
        ev = expense[i] if i < len(expense) else 0
        nv = net[i] if i < len(net) else 0
        ih = max(1, int((iv or 0) / maxv * H))
        eh = max(1, int((ev or 0) / maxv * H))
        sr = srate[i] if i < len(srate) else None
        # 저축률 = 순저축/수입. 수입이 매우 작은 달(부분월 등)은 %가 폭주(-3968%)
        # 하므로 수입>0 & |rate|≤200% 일 때만 라벨 표시, 아니면 '—'(hover엔 실값).
        sr_disp = (f"{sr:.0f}%" if (sr is not None and (iv or 0) > 0 and -200 <= sr <= 200) else "—")
        sr_col = _pf_col(sr) if (sr is not None and (iv or 0) > 0) else "var(--muted)"
        ttl = f"{mo} · 수입 {_pf_won(iv)} · 지출 {_pf_won(ev)} · 순 {_pf_won(nv)} · 저축률 {_pct(sr)}"
        cols += (f'<div class="bg-col" title="{_html.escape(ttl)}">'
                 f'<div class="bg-bars"><div class="bg-bar" style="height:{ih}px;background:var(--pos)"></div>'
                 f'<div class="bg-bar" style="height:{eh}px;background:var(--neg)"></div></div>'
                 f'<div class="bg-mo">{_html.escape(mo[2:])}</div>'
                 f'<div class="bg-sr" style="color:{sr_col}">{sr_disp}</div></div>')
    trend = ('<div class="pf-card"><div class="pf-h">월별 수입·지출 '
             '<span style="font-weight:400;color:var(--muted);font-size:12px">'
             '(<span style="color:var(--pos)">■</span> 수입 · '
             '<span style="color:var(--neg)">■</span> 지출 · 막대 아래 % = <b>저축률</b>(순저축÷수입) · '
             'hover로 상세)</span></div>'
             f'<div class="bg-chart">{cols}</div></div>')

    # 지출 카테고리 도넛 — 금액 큰 상위 8개만 개별, 9위 이하는 '기타N개'로 묶음
    # (hover 시 묶인 항목명·금액 표시 → '기타가 뭐냐' 즉답, 사용자 2026-06-04).
    exp_cats = [c for c in budget.get("expense_cats", []) if (c.get("amount") or 0) > 0]
    donut = ""
    if exp_cats:
        top = exp_cats[:8]
        rest_cats = exp_cats[8:]
        rest = sum(c["amount"] for c in rest_cats)
        if rest > 0:
            _detail = " · ".join(f'{c["항목"]} {_pf_won(c["amount"])}' for c in rest_cats[:25])
            top = top + [{"항목": f"기타 {len(rest_cats)}개", "amount": rest, "_detail": _detail}]
        dtot = sum(c["amount"] for c in top) or 1
        stops, legend, acc = [], [], 0.0
        for i, c in enumerate(top):
            pct = c["amount"] / dtot * 100
            col = _PF_DONUT_COLORS[i % len(_PF_DONUT_COLORS)]
            stops.append(f"{col} {acc:.2f}% {acc + pct:.2f}%")
            _ttl = f' title="{_html.escape(c["_detail"])}"' if c.get("_detail") else ""
            legend.append(f'<div class="pf-leg"{_ttl}><span class="pf-dot" style="background:{col}"></span>'
                          f'{_html.escape(str(c["항목"]))} <b>{_pf_won(c["amount"])}</b> '
                          f'<span style="color:var(--muted)">({pct:.1f}%)</span></div>')
            acc += pct
        # 수입 카테고리 패널 (있으면)
        inc_cats = [c for c in budget.get("income_cats", []) if (c.get("amount") or 0) > 0]
        inc_panel = ""
        if inc_cats:
            inc_panel = ('<div id="bg-inc-cats" style="flex:1 1 200px;min-width:190px">'
                         '<div class="pf-h" style="font-size:13px;color:var(--muted)">💰 수입 구성</div>'
                         + "".join(f'<div class="pf-leg">{_html.escape(str(c["항목"]))} '
                                   f'<b>{_pf_won(c["amount"])}</b></div>' for c in inc_cats[:8]) + '</div>')
        donut = ('<div class="pf-card"><div class="pf-h">지출 카테고리 (기간 합)</div><div class="pf-grid">'
                 f'<div class="pf-hole"><div class="pf-donut" style="background:conic-gradient({",".join(stops)})"></div></div>'
                 f'<div id="bg-exp-cats" style="flex:1 1 240px;min-width:200px">{"".join(legend)}</div>'
                 + inc_panel + '</div></div>')

    # 카테고리 × 월 매트릭스. 뱅샐의 '총계' 행(월수입/월지출 총계 등)은 export 가
    # 비워둬 0 으로 떠 혼란 → **제외**하고, 끝에 우리가 monthly 로 산출한 수입/지출/
    # 순저축 합계 행을 굵게 추가(카드·막대와 동일값). 총계/월평균 열도 render 시점에
    # monthly 로 계산(stored 0 무시 → 재ingest 없이도 즉시 채움). 큰 항목이 위로.
    def _rtot(r):
        return sum(x for x in (r.get("monthly") or []) if x is not None)

    def _ravg(r):
        mv = [x for x in (r.get("monthly") or []) if x is not None]
        return (sum(mv) / len(mv)) if mv else None
    cat_rows = [r for r in budget.get("rows", []) if r.get("kind") in ("income", "expense")]
    cat_rows.sort(key=lambda r: (0 if r["kind"] == "income" else 1, -abs(_rtot(r))))
    mhead = "".join(f'<th class="r">{_html.escape(mo[2:])}</th>' for mo in months)

    def _mcells(arr):
        out = ""
        for i in range(n):
            v = arr[i] if i < len(arr) else None
            out += f'<td class="r">{_pf_won(v) if v is not None else "·"}</td>'
        return out
    body = ""
    for r in cat_rows:
        k = r.get("kind")
        kcol = "var(--pos)" if k == "income" else "var(--neg)"
        body += (f'<tr><td>{_html.escape(str(r.get("항목") or ""))}</td>'
                 f'<td style="color:{kcol};font-size:11px">{_BUDGET_KIND.get(k, k)}</td>'
                 f'<td class="r">{_pf_won(_rtot(r))}</td>'
                 f'<td class="r">{_pf_won(_ravg(r))}</td>{_mcells(r.get("monthly") or [])}</tr>')

    def _total_row(label, arr, color):
        tot = sum(a or 0 for a in arr)
        return (f'<tr style="font-weight:700;border-top:2px solid var(--border)">'
                f'<td>{label}</td><td style="color:{color};font-size:11px">계</td>'
                f'<td class="r">{_pf_won(tot)}</td>'
                f'<td class="r">{_pf_won(tot / n if n else 0)}</td>{_mcells(arr)}</tr>')
    body += _total_row("수입 합계", income, "var(--pos)")
    body += _total_row("지출 합계", expense, "var(--neg)")
    body += _total_row("순저축", net, "var(--muted)")
    matrix = ('<div class="pf-card"><div class="pf-h">현금흐름 상세 (카테고리 × 월)</div>'
              '<div class="bg-mtx"><table class="pf-tbl" id="bg-mtx-tbl">'
              '<thead><tr><th>항목</th><th>구분</th><th class="r">총계</th>'
              f'<th class="r">월평균</th>{mhead}</tr></thead><tbody>{body}</tbody></table></div></div>')

    note = ('<p class="sub" style="margin-top:14px;color:var(--muted);font-size:12px">'
            '뱅크샐러드 현금흐름 export 기준. 수입/지출 분류는 카테고리명 기반 추정이라 '
            '일부 빗나갈 수 있으나 아래 매트릭스는 원본 그대로입니다. '
            '총계·월평균은 월별값으로 산출(뱅샐 요약셀이 비어 있어). '
            '<b>최신 월은 진행 중(부분월)</b>일 수 있어 막대·평균이 작게 보입니다. '
            '수입이 적은 달은 저축률 %가 비현실적으로 커져 — 로 표시. '
            f'기간 {_html.escape(str(budget.get("period") or ""))} · 🕒 업데이트 {updated}</p>')

    _bg_btns = ('<button id="bg-send-tg" class="pf-send" type="button">📤 텔레그램 보내기</button>'
                 '<button id="bg-send-em" class="pf-send" type="button">📧 이메일 보내기</button>'
                 f'<span id="bg-snap-date" hidden>{_html.escape(str(budget.get("period") or updated))}</span>')
    return (_SCREENER_CSS + _PF_CSS + _BUDGET_CSS + '<div class="wrap">' + nav
            + '<div class="pf-title-row"><h1>📒 가계부</h1>' + _bg_btns + '</div>'
            + '<p class="sub">뱅크샐러드 현금흐름 — 월별 수입·지출·저축률 · 자산 대시보드와 별도</p>'
            + cards + trend + donut + matrix + _BG_SEND_JS + note + '</div>')


def _safe_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def regenerate_budget_index() -> None:
    """budget.json → budget.html (ARCHIVE_ROOT). ingest 후 + startup/자정 regen."""
    try:
        from bot.budget import load_budget
        html = _render_budget_page(load_budget())
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "budget.html").write_text(html, encoding="utf-8")
        log.info("dashboard: budget.html regenerated")
    except Exception as exc:
        log.warning("dashboard: budget regen failed: %s", exc)


# ── 분기 GICS / 신규 산업 점검 — 후보 트래킹 (2026-06-01) ────────────────
# bot/screener_gics_check.py 가 분기마다 Pro+web search 로 (a) 공식 GICS
# 분류 변경 + (b) 신규 emerging industry trend 식별 → Telegram 알림 +
# ~/.tradingagents/gics_check_audit.jsonl append. 단순 알림이라 사용자가
# 채택 결정을 잊을 위험 → 본 페이지가 chronological 누적 surface 로 기능.
# 각 candidate 의 suggested_slug 가 registry(list_domains)에 있으면
# '✅ 채택됨' badge, 아니면 '⏳ 미정'. 새 quarterly run 자동 누적 — 추가
# 인프라 0 (jsonl 만 읽음).
_GICS_AUDIT_LOG = Path.home() / ".tradingagents" / "gics_check_audit.jsonl"


def _load_gics_check_runs() -> list[dict]:
    """Read the jsonl audit log written by bot.screener_gics_check.
    Returns newest-first list of entries. Empty list when file missing."""
    if not _GICS_AUDIT_LOG.exists():
        return []
    runs: list[dict] = []
    try:
        with _GICS_AUDIT_LOG.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    runs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        log.warning("dashboard: gics audit load failed: %s", exc)
    runs.sort(key=lambda r: r.get("ts") or "", reverse=True)
    return runs


def _render_gics_candidates_page(runs: list[dict]) -> str:
    """Render gics_candidates.html — quarterly Pro check 결과 누적 surface.
    각 candidate 의 suggested_slug 가 registry 에 이미 있으면 '✅ 채택됨'
    badge. 사용자가 채택/거부 결정을 잊지 않도록 chronological 누적."""
    import html as _html

    # Registry 의 현재 slug set — 채택 여부 판정용. import 실패 시 빈 set
    # (모두 '⏳ 미정' 으로 표시 — graceful degrade).
    try:
        from bot.screener_themes import list_domains
        adopted_slugs = {(d.get("slug") or "").lower() for d in list_domains()}
    except Exception:
        adopted_slugs = set()

    def _norm_slug(s: str) -> str:
        return (s or "").lower().strip()

    def _status_badge(slug: str) -> str:
        if _norm_slug(slug) in adopted_slugs:
            return ('<span style="color:#2ca44b;font-weight:600">'
                    '✅ 채택됨</span>')
        return ('<span style="color:var(--fg-soft)">⏳ 미정</span>')

    # 상단 stats
    total_runs = len(runs)
    total_cost_krw = sum(r.get("cost_krw", 0) or 0 for r in runs)
    last_ts = runs[0].get("ts", "")[:10] if runs else "—"
    # 미채택 candidate 누적 카운트 (사용자가 행동해야 할 항목 수)
    pending_count = 0
    for r in runs:
        rep = r.get("report") or {}
        for it in (rep.get("official_gics_changes") or []):
            if not it.get("already_covered") and _norm_slug(
                it.get("suggested_slug")
            ) not in adopted_slugs:
                pending_count += 1
        for it in (rep.get("emerging_trends") or []):
            if not it.get("already_covered") and _norm_slug(
                it.get("suggested_slug")
            ) not in adopted_slugs:
                pending_count += 1

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="index.html">← NOAH 종목 분석</a>
    · <a href="portfolio.html">💼 자산</a>
    · <a href="budget.html">📒 가계부</a>
    · <a href="screener.html">📊 Bottleneck Screener</a>
    · <a href="screener_domains.html">🗂️ 도메인 목록</a>
  </div>
  <h1>🧬 분기 GICS / 신규 산업 점검</h1>
  <p class="sub">
    bot/screener_gics_check.py 가 분기마다 (3·6·9·12월 5일 09:00 KST)
    Pro+web search 로 식별한 후보. 사용자 검증 후 모듈 add 결정 — 본 페이지
    가 결정 트래킹 surface (Telegram 알림은 휘발성).
  </p>
  <div class="stats">
    <div class="stat"><div class="stat-num">{total_runs}</div>
      <div class="stat-lbl">총 점검 횟수</div></div>
    <div class="stat"><div class="stat-num">₩{int(total_cost_krw):,}</div>
      <div class="stat-lbl">누적 비용</div></div>
    <div class="stat"><div class="stat-num">{last_ts}</div>
      <div class="stat-lbl">마지막 점검일</div></div>
    <div class="stat"><div class="stat-num">{pending_count}</div>
      <div class="stat-lbl">⏳ 미결정 후보</div></div>
  </div>
""")

    if not runs:
        parts.append('<p class="sub" style="margin-top:24px">'
                     '아직 점검 기록이 없습니다. 다음 분기(3·6·9·12월 5일) '
                     '09:00 KST 에 첫 entry 가 추가됩니다.</p>')
        parts.append("</div>")
        return "".join(parts)

    def _render_item(it: dict, kind: str) -> str:
        name = _html.escape(str(it.get("name") or "?"))
        kr = _html.escape(str(it.get("korean_name") or ""))
        slug = _html.escape(str(it.get("suggested_slug") or "?"))
        layer = _html.escape(str(it.get("suggested_layer") or "?"))
        rationale = _html.escape(str(it.get("rationale") or ""))
        badge = _status_badge(it.get("suggested_slug") or "")
        if kind == "trend":
            mc = it.get("estimated_market_cap_usd_billion") or 0
            tickers = ", ".join(it.get("key_tickers") or [])
            meta = (f"시총 ~${mc}B · 종목: {_html.escape(tickers)}")
        else:
            kind_txt = _html.escape(str(it.get("type") or ""))
            parent = _html.escape(str(it.get("parent_sector") or ""))
            eff = _html.escape(str(it.get("effective_date") or ""))
            src = _html.escape(str(it.get("source") or ""))
            meta = (f"유형: {kind_txt} · 상위: {parent} · 효력: {eff}"
                    f" · 출처: {src}")
        return (
            f'<div class="run-card" style="margin:10px 0;padding:12px 14px">'
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'flex-wrap:wrap"><b>{name}</b>'
            f'<span style="color:var(--fg-soft)">({kr})</span>'
            f'{badge}</div>'
            f'<div class="sub" style="margin-top:4px">{meta}</div>'
            f'<div class="sub" style="margin-top:4px">제안: '
            f'<code>{slug}</code> ({layer})</div>'
            f'<div style="margin-top:6px">{rationale}</div>'
            f'</div>'
        )

    for r in runs:
        # 헤더 포맷 (사용자 정책 2026-06-01): 'YYYY-MM-DD, START ~ END 윈도우,
        # ₩cost · GICS 변경 N · 신규 trend N'. 시간(HH:MM:SS) 은 분기 단위
        # 점검이라 부정확해 부담만 — 날짜만으로 충분.
        ts = _html.escape((r.get("ts") or "")[:10])
        cost = int(r.get("cost_krw", 0) or 0)
        rep = r.get("report") or {}
        win_start = _html.escape(str(rep.get("window_start") or "?"))
        win_end = _html.escape(str(rep.get("window_end") or "?"))
        summary = _html.escape(str(rep.get("summary") or ""))
        gics_items = [it for it in (rep.get("official_gics_changes") or [])
                      if not it.get("already_covered")]
        trend_items = [it for it in (rep.get("emerging_trends") or [])
                       if not it.get("already_covered")]

        parts.append(f"""
<details class="run-wrap">
  <summary class="run-summary">
    <span class="run-date">{ts},</span>
    <span class="run-meta">{win_start} ~ {win_end} 윈도우, ₩{cost:,}
      · GICS 변경 {len(gics_items)} · 신규 trend {len(trend_items)}</span>
  </summary>
  <div class="run-body">
""")
        if summary:
            parts.append(f'<div class="sub" style="margin:8px 0 14px 0">'
                         f'{summary}</div>')
        if gics_items:
            parts.append('<h3 style="margin:14px 0 6px 0">━━━ 공식 GICS '
                         '변경 ━━━</h3>')
            for it in gics_items:
                parts.append(_render_item(it, "gics"))
        else:
            parts.append('<p class="sub">공식 GICS 변경 없음.</p>')
        if trend_items:
            parts.append('<h3 style="margin:14px 0 6px 0">━━━ 신규 industry '
                         'trend ━━━</h3>')
            for it in trend_items:
                parts.append(_render_item(it, "trend"))
        else:
            parts.append('<p class="sub">신규 trend candidate 없음.</p>')
        parts.append('</div></details>')

    parts.append("</div>")
    return "".join(parts)


def regenerate_gics_candidates_index() -> None:
    """Scan ~/.tradingagents/gics_check_audit.jsonl → write
    gics_candidates.html under ARCHIVE_ROOT. Called from
    _periodic_dashboard_refresh + _on_startup. Errors swallowed."""
    try:
        runs = _load_gics_check_runs()
        html = _render_gics_candidates_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "gics_candidates.html").write_text(
            html, encoding="utf-8"
        )
        log.info("dashboard: gics_candidates.html regenerated (%d runs)",
                 len(runs))
    except Exception as exc:
        log.warning("dashboard: gics_candidates regen failed: %s", exc)
