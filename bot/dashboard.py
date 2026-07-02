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
    # Per-subsystem breakdown (분석 / Screener / …) so the main dashboard
    # surfaces where the total bill is coming from. Screener Pro calls
    # land in usage.jsonl with subsystem='screener'. (SV 행 제거 2026-06-12
    # — Standard View 폐기 #148 + 소스 삭제.)
    _sub_keys = {"분석": 0.0, "Screener": 0.0, "Daily Byte": 0.0,
                 "부동산": 0.0, "블로그": 0.0, "관계후보": 0.0,
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
                "cheongyak": "부동산", "realestate": "부동산",
                "blog": "블로그"}.get(_subsys, "분석"))
        if rec_day.startswith(month_prefix):
            month_cost_usd += cost
            m = r.get("model") or "unknown"
            month_cost_by_model[m] = month_cost_by_model.get(m, 0.0) + cost
            month_cost_by_sub_usd[sub] += cost
            if rec_day == today_str:
                today_cost_usd += cost
                today_cost_by_sub_usd[sub] += cost

    # Standard View 비용 reader 제거 (2026-06-12) — SV 폐기(#148)로 신규
    # 비용 0, 소스 트리 삭제와 함께 합산도 정리 (trailing 분 제외).

    # 한국 수출입(trade) cost — 별도 repo(stock-trade)가 남기는 usage.jsonl
    # 을 읽어 메인 합산에 포함 (사용자 정책 2026-06-02 — nav 에 링크된 비용
    # -발생 surface 는 메인 대시보드 총합에 합산). 경로: $TRADE_DATA_DIR/
    # usage.jsonl, 미설정 시 ~/.trade/usage.jsonl. 그 repo 의 스키마를
    # 우리가 100% 통제하지 못하므로 방어적으로 cost_usd / cost_krw 양쪽 +
    # ts(epoch) / date(YYYY-MM-DD str) 양쪽 tolerant. 파일 부재·다른
    # 호스트 시 silent skip. 모델 분포는 미상이라 by_model 에 미합산(총합·
    # subsystem 분포만).
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
                    # kg 관계후보(블로그·DART 자동발굴, kind=kg_*)는 같은 trade
                    # usage.jsonl 이지만 수출입(insight)과 분리 — kind 로 버킷 구분
                    # (사용자 2026-06-24 '블로그도 공짜 아니니 별도 비용카드 + 메인
                    # 합산'). kind 미기록 옛 레코드는 수출입(기존 동작 보존).
                    _bucket = ("관계후보"
                               if (rec.get("kind") or "").startswith("kg")
                               else "수출입")
                    if rec_day_tr.startswith(month_prefix):
                        month_cost_usd += cost_usd_tr
                        month_cost_by_sub_usd[_bucket] += cost_usd_tr
                        if rec_day_tr == today_str:
                            today_cost_usd += cost_usd_tr
                            today_cost_by_sub_usd[_bucket] += cost_usd_tr
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
    # Per-subsystem breakdown. Surface only buckets with non-zero
    # this-month cost — keeps the sub-label compact. (SV 제거 2026-06-12.)
    sub_parts: list[str] = []
    for key, label in [("분석", "분석"), ("Screener", "screener"), ("Daily Byte", "Daily Byte"),
                       ("부동산", "부동산"), ("블로그", "블로그"),
                       ("관계후보", "관계후보"), ("수출입", "수출입")]:
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
  /* '← 홈으로' 가로채기 제거 (사용자 2026-06-15 '또 새로고침처럼') — history.back()
     은 bfcache 불안정·다단이동 시 엉뚱한 페이지. '원래 자리로'는 market.html 의
     스크롤 복원(sessionStorage)으로 해결(어떤 경로로 도착해도 마지막 위치 복귀). */
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
/* Linear 스타일 디자인 토큰 (2026-06-27, design-spec 이식). 변수명 불변·값만
   교체 → var() 쓰는 전 요소 자동 리스타일. 라이트 기본 + data-theme=dark 오버라이드.
   별/메모/알람·삭제·차트 등 semantic 색은 spec 도 유지(여기 미변경). */
:root {
  --fg: #282a30; --fg-soft: #8a8f98; --bg: #f7f8f9; --card: #ffffff;
  --surface-2: #f0f1f3;
  --border: #e8e8ea; --border-input: #e0e1e4; --border-strong: #d8dade;
  --accent: #5e6ad2; --accent-hover: #515dc4;
  --focus-ring: rgba(94,106,210,0.13);
  --radius: 10px; --radius-sm: 8px;
}
:root[data-theme="dark"] {
  --fg: #e2e3e6; --fg-soft: #8a8f98; --bg: #0b0c0e; --card: #141518;
  --surface-2: #1a1b1f;
  --border: #26272b; --border-input: #2a2c31; --border-strong: #34363c;
  --accent: #7c84e8; --accent-hover: #9aa2f0;
  --focus-ring: rgba(124,132,232,0.18);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
    "Pretendard", "Helvetica Neue", "Segoe UI", "Apple Color Emoji",
    "Segoe UI Emoji", "Noto Color Emoji", "Twemoji Mozilla", sans-serif;
  color: var(--fg); background: var(--bg); line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.014em; }
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
  background: var(--card); border: 1px solid var(--border-input); border-radius: 8px;
  outline: none; transition: border-color 0.12s, box-shadow 0.12s;
}
.search-bar input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--focus-ring); }
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
.search-bar button.run-btn { background: #3b82f6; }
.search-bar button.run-btn:hover { background: #2563eb; }
:root[data-theme="dark"] .search-bar button.run-btn { background: #2563eb; }
:root[data-theme="dark"] .search-bar button.run-btn:hover { background: #1d4ed8; }
.search-bar button:disabled { opacity: 0.55; cursor: wait; }
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


_INDEX_CSV_JS = """<script>
(function(){
  var btn=document.getElementById('csv-btn');
  if(!btn)return;
  btn.addEventListener('click',function(){
    // 인라인 임베드 → 외부 JSON fetch(성능 2026-07-03: 전 리포트 전문 2중
    // 임베드가 index 최대 중량원이었음). 클릭 시에만 다운로드.
    btn.disabled=true; btn.textContent='⏳';
    fetch('analysis_csv.json',{cache:'no-store'})
      .then(function(r){ if(!r.ok) throw 0; return r.json(); })
      .then(function(rows){ buildCsv(rows); })
      .catch(function(){ alert('CSV 데이터를 불러오지 못했습니다.'); })
      .then(function(){ btn.disabled=false; btn.textContent='⬇ CSV'; });
  });
  function buildCsv(rows){
    if(!rows.length){alert('내보낼 분석 기록이 없습니다.');return;}
    var cols=Object.keys(rows[0]);
    function esc(v){v=(v==null?'':String(v));return /[",\\n\\r]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
    var lines=[cols.join(',')];
    rows.forEach(function(r){lines.push(cols.map(function(c){return esc(r[c]);}).join(','));});
    var csv='\\ufeff'+lines.join('\\r\\n');   // BOM = Excel 한글 깨짐 방지
    var blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a');
    a.href=url;a.download='noah_분석_'+new Date().toISOString().slice(0,10)+'.csv';
    document.body.appendChild(a);a.click();document.body.removeChild(a);
    setTimeout(function(){URL.revokeObjectURL(url);},1000);
  }
})();
</script>"""


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
  // 과거 월은 lazy 프래그먼트(details.month[data-lazy], 성능 2026-07-03) —
  // 로드될 때마다 카드/일/월 참조를 재수집(rescan). 총 건수는 서버가 준
  // data-total(전체 아카이브 기준 — DOM 카드 수는 로드된 만큼뿐).
  let cards = [], days = [], monthsG = [], cardData = [];
  const total = parseInt(statusEl.dataset.total || '0', 10) || 0;
  const MAX_SNIPPETS = 80;
  var marketFilter = 'ALL';
  const mfBtns = Array.from(document.querySelectorAll('.mf-btn'));
  const lazyMonths = Array.from(document.querySelectorAll('details.month[data-lazy]'));
  // __failed 는 pending 에서 제외 — 404/오프라인 시 applyFilter 가 loadAll 을
  // 무한 재귀(요청 폭주)하던 것 차단(리뷰 finding #1). 수동으로 접었다 펼치면
  // __failed 리셋 → 재시도.
  function pendingMonths(){ return lazyMonths.filter(function(m){ return !m.__loaded && !m.__failed; }); }
  function failedCount(){ return lazyMonths.filter(function(m){ return m.__failed; }).length; }
  function loadMonth(m){
    if (m.__loaded) return Promise.resolve();
    if (m.__loading) return m.__loading;
    var body = m.querySelector('.month-body');
    m.__loading = fetch(m.dataset.lazy, {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){
        body.innerHTML = html; m.__loaded = true; m.__failed = false;
        // rescan/★도구 예외가 fetch catch 로 떨어져 방금 넣은 본문을 '실패'
        // 문구로 덮고 __loaded=true 라 영영 재시도 불가되던 것 방지(finding #4).
        try { rescan(); if (window.__impApply) window.__impApply(); } catch(e) {}
      })
      .catch(function(){
        m.__loading = null; m.__failed = true;
        body.innerHTML = '<div class="lazy-note" style="color:var(--fg-soft);padding:10px 4px">불러오기 실패 — 접었다 다시 펼쳐보세요.</div>';
      });
    return m.__loading;
  }
  function loadAllMonths(){ return Promise.all(pendingMonths().map(loadMonth)); }
  lazyMonths.forEach(function(m){
    m.addEventListener('toggle', function(){
      if (m.open && !m.__loaded) { m.__failed = false; loadMonth(m).then(applyFilter); }
    });
  });
  function matchesMarket(c) {
    return marketFilter === 'ALL' || c.dataset.market === marketFilter;
  }

  // Parse each card's body line index once per rescan. Synthetic 'metadata'
  // line adds the visible card-row text (ticker chip + stance + rating)
  // so a plain ticker query like 'NVDA' still surfaces the card as
  // a snippet rather than 0 matches when the body section labels
  // don't contain 'NVDA' literally.
  function rescan() {
    cards = Array.from(document.querySelectorAll('.card'));
    days = Array.from(document.querySelectorAll('details.day'));
    monthsG = Array.from(document.querySelectorAll('details.month'));
    cardData = cards.map(function(c) {
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
  }
  rescan();

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
      // 미로드 lazy 월은 day 가 없어도 보여야(숨기면 영영 펼칠 수 없음).
      if (m.dataset.lazy && !m.__loaded) { m.style.display = ''; continue; }
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
    if (raw.charAt(0) === '/') {  // 명령 모드 — 카드 필터 대신 콘솔 안내
      showCardsMode();
      statusEl.textContent = '⌨️ 명령 모드 — Enter 또는 [분석] 으로 실행 (예: /usage · /portfolio · /screener ai · /watchlist)';
      return;
    }
    if (!raw) { showCardsMode(); return; }
    // 검색은 전체 아카이브 대상 — 미로드 과거 월이 있으면 먼저 불러오고 재실행
    // (lazy 분리로 검색이 최신 달만 보는 회귀 방지).
    if (pendingMonths().length) {
      statusEl.textContent = '🔎 전체 기록 불러오는 중… (' + pendingMonths().length + '개월)';
      loadAllMonths().then(applyFilter);
      return;
    }
    showSnippetsMode(raw);
    // 로드 실패 월 가시화 — 결과가 '전체'처럼 보이는 착시 방지(finding #7).
    var fc = failedCount();
    if (fc) statusEl.textContent += ' · ⚠️ ' + fc + '개월 로드 실패(해당 월 제외)';
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
      // 시장 필터도 전체 대상 — 미로드 월 로드 후 적용(정확한 노출/카운트).
      if (marketFilter !== 'ALL' && pendingMonths().length) {
        statusEl.textContent = marketFilter + ' 필터 — 전체 기록 불러오는 중…';
        loadAllMonths().then(applyFilter);
        return;
      }
      applyFilter();
    });
  });

  // Card deletion: POST /api/delete with {date, ticker}, then remove the
  // card from the DOM on success. We don't location.reload() because
  // that would kick the user back to the top of the page; the
  // regenerate_index() call on the server side has already rewritten
  // index.html so a manual refresh later picks up everything.
  // 위임 방식 — lazy 로드된 과거 월 카드의 🗑️ 도 배선되게(성능 2026-07-03).
  document.addEventListener('click', function(ev) {
      const btn = ev.target.closest && ev.target.closest('.del-btn');
      if (!btn) return;
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

# ── 대시보드 명령 콘솔 JS ─────────────────────────────────────────────────
# 입력이 '/' 로 시작하면 텔레그램 명령으로 실행(api/run kind=command → 봇이
# 캡처 shim 으로 핸들러 실행 → api/command_result 폴링), 아니면 기존 주작업
# (분석/스크리너). 결과는 입력창 아래 패널(panelId)에 표시. 상대경로 fetch.
_CONSOLE_JS = r"""
(function(){
  if (document.getElementById('noah-cmd-css')) return;
  var st = document.createElement('style'); st.id = 'noah-cmd-css';
  st.textContent = '.cmd-panel{background:var(--card,#1a1d24);border:1px solid var(--border,#2a2e37);border-radius:10px;margin:0 0 18px;overflow:hidden}'
    + '.cmd-panel .cmd-hd{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border,#2a2e37);background:var(--bg,#0f1115)}'
    + '.cmd-panel .cmd-title{font-size:13px;font-weight:600;color:var(--accent,#3b82f6);font-family:ui-monospace,Menlo,monospace;word-break:break-all}'
    + '.cmd-panel .cmd-x{background:none;border:none;color:var(--fg-soft,var(--muted,#9aa));cursor:pointer;font-size:14px;padding:2px 6px;flex-shrink:0}'
    + '.cmd-panel .cmd-body{margin:0;padding:12px 14px;font-size:13px;line-height:1.55;white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Menlo,monospace;max-height:60vh;overflow:auto}'
    + '.run-btn.run-busy{background:#e2574c !important;cursor:wait}';
  (document.head||document.documentElement).appendChild(st);
})();
function noahEsc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; }
function noahShowPanel(panel, title, bodyLines){
  if (!panel) { alert((title?title+'\n\n':'') + (bodyLines||[]).join('\n')); return; }
  var html = '<div class="cmd-hd"><span class="cmd-title">' + noahEsc(title) + '</span>'
    + '<button type="button" class="cmd-x" title="닫기">✕</button></div>';
  if (bodyLines && bodyLines.length){
    html += '<pre class="cmd-body">' + bodyLines.map(noahEsc).join('\n') + '</pre>';
  }
  panel.innerHTML = html; panel.style.display = 'block';
  var x = panel.querySelector('.cmd-x');
  if (x) x.addEventListener('click', function(){ panel.style.display='none'; panel.innerHTML=''; });
}
function noahPollResult(id, cmd, panel, tries){
  if (tries > 90){ noahShowPanel(panel, cmd, ['⚠️ 시간 초과 — 텔레그램 채널을 확인하세요.']); return; }
  fetch('api/command_result?id=' + encodeURIComponent(id), {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d && d.done){ noahShowPanel(panel, cmd, (d.lines && d.lines.length) ? d.lines : ['(출력 없음)']); return; }
      setTimeout(function(){ noahPollResult(id, cmd, panel, tries+1); }, 2000);
    })
    .catch(function(){ setTimeout(function(){ noahPollResult(id, cmd, panel, tries+1); }, 2000); });
}
function noahRunCommand(cmd, panel){
  var word = cmd.replace(/^\/+/, '').split(/\s+/)[0].toLowerCase();
  if (word === 'screener'){
    if (!confirm("'" + cmd + "'\nBottleneck Screener 실행 — ~3-5분 · ~₩330. 계속할까요?")) return;
  }
  noahShowPanel(panel, cmd, ['⏳ 실행 중…']);
  fetch('api/run', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({kind:'command', q: cmd})})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || !d.ok || !d.id){ noahShowPanel(panel, cmd, ['⚠️ ' + ((d&&d.error)||'요청 실패')]); return; }
      noahPollResult(d.id, cmd, panel, 0);
    })
    .catch(function(e){ noahShowPanel(panel, cmd, ['⚠️ 요청 실패: ' + e]); });
}
function noahConsoleSetup(opts){
  var inp = document.getElementById(opts.inputId);
  var btn = document.getElementById(opts.btnId);
  var panel = opts.panelId ? document.getElementById(opts.panelId) : null;
  var statusEl = opts.statusId ? document.getElementById(opts.statusId) : null;
  function runPrimary(q){
    if (!q && opts.requireQuery){ if (inp) inp.focus(); alert(opts.emptyMsg || '입력 후 실행하세요.'); return; }
    var label = q || opts.defaultLabel || '';
    if (!confirm((label ? "'" + label + "'\n" : '') + (opts.confirmText || '실행할까요?'))) return;
    var orig = btn ? btn.textContent : '';
    if (btn){ btn.disabled = true; btn.textContent = '…'; }
    fetch('api/run', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kind: opts.runKind, q: q})})
      .then(function(r){ return r.json().then(function(d){ return {st:r.status, d:d}; }); })
      .then(function(x){
        if (x.d && x.d.ok){
          var note = x.d.dup ? '이미 같은 요청이 대기 중입니다.'
            : ((x.d.q ? x.d.q + ' — ' : '') + (opts.okMsg || '요청 접수. 결과는 텔레그램 채널에 게시됩니다.'));
          if (statusEl) statusEl.textContent = '✅ ' + note; else alert('✅ ' + note);
          if (btn && x.d.id){
            // 작업중 = 빨간 버튼, 완료 시 원복(사용자 2026-06-11). 봇이
            // 완료 시점에 같은 id 로 result 를 기록 → 5초 폴링.
            btn.classList.add('run-busy'); btn.textContent = '작업중';
            (function pollDone(tries){
              if (tries > 300){
                btn.classList.remove('run-busy'); btn.disabled=false; btn.textContent=orig;
                if (statusEl) statusEl.textContent = '⚠️ 완료 확인 시간 초과 — 텔레그램 채널을 확인하세요.';
                return;
              }
              fetch('api/command_result?id=' + encodeURIComponent(x.d.id), {cache:'no-store'})
                .then(function(r){ return r.json(); })
                .then(function(d){
                  if (d && d.done){
                    btn.classList.remove('run-busy'); btn.disabled=false; btn.textContent=orig;
                    if (statusEl) statusEl.textContent = (d.lines && d.lines.length ? d.lines[0] : '✅ 완료');
                    return;
                  }
                  setTimeout(function(){ pollDone(tries+1); }, 5000);
                })
                .catch(function(){ setTimeout(function(){ pollDone(tries+1); }, 5000); });
            })(0);
          } else if (btn){ btn.disabled=false; btn.textContent=orig; }
        } else {
          if (btn){ btn.disabled=false; btn.textContent=orig; }
          alert('⚠️ ' + ((x.d && x.d.error) || ('요청 실패 (HTTP ' + x.st + ')')));
        }
      })
      .catch(function(e){ if (btn){ btn.disabled=false; btn.textContent=orig; } alert('⚠️ 요청 실패: ' + e); });
  }
  function go(){
    var raw = (inp && inp.value || '').trim();
    if (raw.charAt(0) === '/'){ noahRunCommand(raw, panel); return; }
    runPrimary(raw);
  }
  if (btn) btn.addEventListener('click', go);
  if (inp) inp.addEventListener('keydown', function(e){
    if (e.key === 'Enter'){
      var raw = (inp.value || '').trim();
      if (raw.charAt(0) === '/'){ e.preventDefault(); noahRunCommand(raw, panel); }
    }
  });
}
"""

# index.html: 분석 버튼 = 티커 분석(슬래시 없이), '/' 입력 = 텔레그램 명령 콘솔.
_INDEX_RUN_JS = _CONSOLE_JS + r"""
noahConsoleSetup({
  inputId: 'search', btnId: 'analyze-btn', statusId: 'status', panelId: 'cmd-panel',
  runKind: 'analyze', requireQuery: true,
  emptyMsg: '분석할 종목(티커 또는 종목명)을 검색창에 입력하세요.\n예: NVDA · 삼성전자 · 005930.KS\n또는 / 명령 (예: /usage · /portfolio · /screener ai)',
  confirmText: '전체 분석을 시작할까요?\n· 소요 ~3분 · 비용 ~₩100-150 (캐시 시 무료)\n· 결과: 텔레그램 채널 + 이 아카이브(자동 갱신)',
  okMsg: '분석 요청 접수 — ~3분 후 텔레그램 채널과 이 아카이브에 게시됩니다.'
});
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
<title>🚨 오류 / 미완성 기록</title>
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


def _render_index(records: list[dict]) -> tuple[str, dict[str, str]]:
    """→ (index.html 본문, fragments). fragments = 과거 월 lazy 프래그먼트
    (idx_m_YYYY-MM.html) + analysis_csv.json — regenerate_index 가 기록.
    (성능 감사 2026-07-03: 최신 달만 인라인, CSV 인라인 임베드 제거.)"""
    by_date: dict[str, list[dict]] = {}
    for r in records:
        by_date.setdefault(r["trade_date"], []).append(r)

    # Market counts for filter buttons
    _market_counts: dict[str, int] = {}
    for _r in records:
        _mk = _ticker_market(_r.get("ticker", ""))
        _market_counts[_mk] = _market_counts.get(_mk, 0) + 1

    csv_rows: list[dict] = []   # 분석 전체 → CSV 내보내기(사용자 2026-06-14)
    fragments: dict[str, str] = {}   # 과거 월 lazy 프래그먼트 + CSV JSON(외부화)
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
        # 월별 day-블록 수집 → 최신 달만 인라인, 과거 달은 프래그먼트 파일로
        # 분리(펼칠 때/검색할 때 fetch — 성능 감사 2026-07-03 '과거 월 lazy').
        # data-lines(카드당 최대 ~50KB) 전송·파스가 아카이브 길이에 비례해
        # 폭증하던 것을 첫 로드에서 제거.
        _months_html: dict[str, list[str]] = {}
        _month_order: list[str] = []
        for date in sorted(by_date.keys(), reverse=True):
            _cur_month_idx = (date or "")[:7]
            if _cur_month_idx not in _months_html:
                _months_html[_cur_month_idx] = []
                _month_order.append(_cur_month_idx)
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
                _res = resolved_lookup.get((date, ticker)) or {}
                csv_rows.append({
                    "분석일": date, "시각": time_str, "종목": ticker,
                    "종목명": kr_name or "", "시장": _card_mkt,
                    "판정": rating, "Stance": stance or "",
                    "5거래일수익률%": _res.get("raw", ""),
                    "알파%p": _res.get("alpha", ""),
                    "비용원": int(round(float(rec.get("cost_krw", 0) or 0))) or "",
                    # 요약 전문 + 전체 리포트 전문 (사용자 2026-06-14 '요약과 전체
                    # 리포트까지 모두 포함'). CSV 셀에 멀티라인 — JS esc 가 따옴표 처리.
                    "요약": summary_text.strip(),
                    "전체리포트": (rec.get("full_report") or "").strip(),
                })
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
            _months_html[_cur_month_idx].append(f"""
            <details class="day" open>
              <summary class="day-head">
                <span>📅 {_format_date_kr(date)}</span>
                <span class="count">{len(day_records)} 건</span>
              </summary>
              <div class="cards">{"".join(cards)}</div>
            </details>
            """)
        # 최신 달(또는 이번 달)만 인라인+open, 과거 달은 <details data-lazy> —
        # 클라 JS 가 펼침/검색 시 fetch. 프래그먼트는 regenerate_index 가 기록.
        for _i, _ym in enumerate(_month_order):
            _head = (f'<summary class="month-head">'
                     f'<span>📆 {_month_kr(_ym)}</span>'
                     f'<span class="count">{_month_counts_idx.get(_ym, 0)} 건</span>'
                     f'</summary>')
            _body = "".join(_months_html[_ym])
            if _i == 0 or _ym == _this_month_idx:
                sections.append(f'<details class="month" open>{_head}'
                                f'<div class="month-body">{_body}</div></details>')
            else:
                _frag = f"idx_m_{_ym}.html"
                fragments[_frag] = _body
                sections.append(
                    f'<details class="month" data-lazy="{_frag}">{_head}'
                    f'<div class="month-body"><div class="lazy-note" '
                    f'style="color:var(--fg-soft);padding:10px 4px">펼치면 '
                    f'불러옵니다… ({_month_counts_idx.get(_ym, 0)} 건)</div>'
                    f'</div></details>')
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
                <span class="count">{len(orphans)} 건</span>
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
    # Nav group 2 links (NOAH index.html is current page → omit self).
    # 도메인 목록은 제거(사용자 2026-06-10) — Screener 페이지 nav 에서 접근.
    errors_link = (
        ' · <a href="market.html">🌍 홈</a>'
        ' · <a href="asia.html">🏯 ASIA</a>'   # 홈 옆 ASIA — 여기(NOAH 종목분석)만 (사용자 2026-06-15)
        ' · <a href="screener.html">📊 Screener</a>'
        ' · <a href="paper.html">🔔 워치리스트</a>'
        ' · <a href="dart_feed.html">📋 DART 공시</a>'
        ' · <a href="valuechain.html">🔗 밸류체인</a>'
        f' · <a href="trade/">🌏 수출입</a>'
        ' · <a href="daily_byte.html">📰 Daily Byte</a>'
        ' · <a href="reddit_insider.html">📨 미국 레딧</a>'
        ' · <a href="blog.html">📝 블로그</a>'
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
    # 전체 분석 기록 CSV(엑셀) — 인라인 임베드 대신 외부 JSON(analysis_csv.json,
    # 성능 감사 2026-07-03: 전 리포트 전문이 페이지에 2번 들어가던 최대 중량원).
    # csv-btn 클릭 시 fetch — 다운로드 UX 동일, 첫 로드에서 수 MB 제거.
    fragments["analysis_csv.json"] = json.dumps(csv_rows, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>🦉 주식분석 아카이브</title>
<script>{_THEME_JS}</script>
<style>{_INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>🦉 주식분석 아카이브</h1>
  <p class="sub">카드 클릭 시 전체 리포트{errors_link}</p>
  {stats_panel}
  <div class="search-bar">
    <input id="search" type="text" placeholder="종목·본문 검색 · 분석은 [분석] · '/' 로 명령 (예: /usage · /portfolio · /screener ai)" autocomplete="off" spellcheck="false">
    <button id="clear-btn" type="button" title="검색 초기화">초기화</button>
    <button id="analyze-btn" type="button" class="run-btn" title="슬래시 없이 종목 입력 후 전체 분석(텔레그램 /티커 와 동일). '/' 입력 시엔 그 명령을 실행">분석</button>
    <button id="csv-btn" type="button" title="전체 분석 기록을 CSV(엑셀)로 내보내기">⬇ CSV</button>
  </div>
  {_INDEX_CSV_JS}
  {market_filter_html}
  <p id="status" class="status-line" data-total="{len(records)}">총 {len(records)}건의 분석 기록</p>
  <div id="cmd-panel" class="cmd-panel" style="display:none"></div>
  <div id="snippets" class="snippets" style="display:none"></div>
  <div id="empty-search" class="empty-search">검색 결과가 없습니다.</div>
  {body}
  {footer_html}
</div>
<script>{_INDEX_JS}</script>
<script>{_INDEX_RUN_JS}</script>
{_imp_cfg("analysis", head_sel=".card-row", id_attrs=["ticker", "date"], search_sel="#search")}
{_IMPORTANT_BLOCK}
</body>
</html>
"""
    return html, fragments


_DETAIL_CSS = _BASE_CSS + """
.q-dot { display:inline-block; width:9px; height:9px; border-radius:50%;
  margin-left:8px; vertical-align:middle; }
.q-dot-load { background:#e2574c; animation:qpulse 1s ease-in-out infinite; }
.q-dot-done { background:#26a69a; }
@keyframes qpulse { 0%,100%{opacity:1} 50%{opacity:0.25} }
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
.chart-head-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
#chart-live-slot { display: flex; align-items: center; gap: 6px;
  font-size: 11px; color: var(--fg-soft); white-space: nowrap; }
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
.chart-legend .lg-close { color: var(--fg); }
.chart-legend .lg-ema   { color: #2563eb; }
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
/* 탭은 항상 한 줄 — 넘치면 가로 스크롤(모바일·다탭 친화, 사용자 2026-06-28).
   활성 밑줄은 box-shadow inset 으로 그려 스크롤 컨테이너에서 안 잘림(옛 margin
   -2px 오버플로 기법은 overflow:hidden 과 충돌해 제거). 스크롤바는 얇고 은은하게. */
.si-tabs {
  display: flex; flex-wrap: nowrap; gap: 2px 2px;
  border-bottom: 2px solid var(--border); margin: 0 0 16px;
  overflow-x: auto; overflow-y: hidden;
  -webkit-overflow-scrolling: touch; scrollbar-width: thin;
}
.si-tabs::-webkit-scrollbar { height: 4px; }
.si-tabs::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
.si-tabs::-webkit-scrollbar-track { background: transparent; }
.si-tab {
  padding: 8px 14px; font-size: 13px; color: var(--fg-soft); cursor: pointer;
  white-space: nowrap; flex: 0 0 auto;          /* 칩 단위 — 한글 글자단위 줄바꿈 방지 */
  font-family: inherit; background: none; border: none;
}
.si-tab:hover { color: var(--fg); }
.si-tab.active { color: var(--accent); font-weight: 600; box-shadow: inset 0 -2px 0 var(--accent); }
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
.si-news-sub { font-size: 12px; color: var(--fg-soft); margin-top: 4px; line-height: 1.5; }
.si-news-meta { font-size: 11px; color: var(--fg-soft); margin-top: 4px; }
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
        <button class="chart-tf-btn" data-kind="range" data-val="1d">1일</button>
        <button class="chart-tf-btn" data-kind="range" data-val="1wk">1주일</button>
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
        <div class="chart-head-row">
          <div id="chart-headline" class="chart-headline"></div>
          <span id="chart-live-slot"></span>
        </div>
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
      상단 헤드라인=현재가·기간수익률(절대+%)·거래량 · OHLC 바=날짜·시고저종·일간등락(마우스 올리면 그 봉, 떼면 마지막 봉) · 현재가=장중 라이브(KR은 네이버 실시간 우선, 그 외 yfinance ~15분 지연) · 시점가=분석일 종가 · 분석 후=시점가 대비 현재가 변동% · 기간=표시 구간 수익률(YTD=연초 이후 포함) · 진입/손절/목표=트레이드 플랜 · ▲매수/▼매도/●보유 마커=우리 과거 추천(+5거래일 결과) · ■ 작은 사각=공시(수주·소송 초록·시설투자 파랑·주주환원 보라·자본변동 주황·M&A 청록·리스크 빨강·최대주주변경 분홍, hover 시 차트 아래에 종류·제목·원문 링크) · 차트 아래 캡션=범위·봉 개수·날짜범위·출처 · 마우스 hover로 그 날 값 확인 · 지표 버튼으로 캔들/이평선/볼린저/거래량/RSI/MACD/로그/공시 on/off (새로고침 시 기본값으로 회귀)
    </div>
    <details class="chart-guide">
      <summary>ℹ️ 차트 보는 법 — 라인·지표·조작 자세히</summary>
      <div class="cg-sec"><b>가격 라인 / 기준선</b>
        <ul>
          <li><span class="k" style="color:var(--fg)">현재가</span> — 장중 라이브(KR은 네이버 실시간 우선, 그 외 yfinance ~15분 지연). 우측 축에 항상 표시. 이상 시세(분할·조정 오류 등)는 자동으로 걸러져 직전 종가로 대체.</li>
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
          <li><span class="k">기간 N</span> — 지금 보이는 구간(1일·1주일·1개월·3개월 등)의 수익률. 범위를 바꾸면 그 구간 기준으로 갱신. 1일=5분봉, 1주일=15분봉, 1개월=1시간봉, YTD=연초(1/1) 이후.</li>
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
      <div class="cg-sec"><b>보조지표 버튼</b> — 페이지 안에서 자유롭게 켜고 끌 수 있습니다. 새로고침하면 기본값(캔들·이평선·거래량·RSI·MACD)으로 돌아갑니다.
        <ul>
          <li><span class="k">캔들</span> — 라인 ↔ 캔들(시·고·저·종) 전환.</li>
          <li><span class="k">이평선</span> — <span style="color:#2563eb">10 EMA</span>(단기) · <span style="color:#3ec46d">50 SMA</span>(중기) · <span style="color:#e2574c">200 SMA</span>(장기) 추세선.</li>
          <li><span class="k" style="color:#7890c8">볼린저</span> — 20일·2σ 밴드. 상단 부근=과열, 하단 부근=과매도, 폭=변동성.</li>
          <li><span class="k">거래량</span> — 가격 아래 막대(상승 초록/하락 빨강).</li>
          <li><span class="k" style="color:#b07cff">RSI</span> — 하단 별도 패널. 70↑ 과열 · 30↓ 과매도(흔한 해석).</li>
          <li><span class="k" style="color:#4c9aff">MACD</span>(이동평균수렴확산) — 하단 별도 패널. <b>MACD선=12일EMA−26일EMA</b>(단기·장기 추세 격차), <b>시그널선=MACD의 9일EMA</b>, <b>막대(히스토그램)=MACD−시그널</b>. ① MACD가 시그널 <b>위로 교차(골든)=상승 모멘텀</b>, 아래로(데드)=하락. ② 0선 위=상승추세·아래=하락추세. ③ 히스토그램이 0에서 커질수록 모멘텀 강화, 줄면 약화. ④ 가격은 신고가인데 MACD는 더 낮으면 <b>다이버전스</b>(추세 약화 경고). 추세추종 보조지표라 횡보장선 신호가 잦음.</li>
          <li><span class="k">로그</span> — 세로축 로그 스케일. 긴 기간 %변동 비교에 유리.</li>
          <li><span class="k">공시</span> — 공시 마커 표시/숨김(위 '공시 마커' 참고).</li>
        </ul>
      </div>
      <div class="cg-sec"><b>기간 · 봉 · 조작</b>
        <ul>
          <li><span class="k">일/주/월봉</span> + <span class="k">1일·1주일·1개월·3개월·6개월·YTD·1년·3년·5년·전체</span> 범위. 단기 기간은 최적 봉 자동 매핑: 1일→5분봉(KR 종목은 네이버 실시간 분봉), 3개월 이상→일봉. 수동으로 봉 종류를 바꿀 수도 있음. 주·월봉은 이평선·RSI 등이 그 단위로 재계산(일봉만 본문 TECHNICAL SNAPSHOT과 일치). 가격 시리즈는 Yahoo가 1차이며, Yahoo에 데이터가 없는 종목은 네이버→KIS 일봉으로 자동 폴백(셋 다 없으면 '데이터 없음').</li>
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
  var IND_DEFAULT = { candle:true, ma:true, bb:false, vol:true, rsi:true, macd:true, log:false, events:false };
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
  // 현재가(주가) = 전경색 — 가장 또렷한 주인공, 모든 지표선과 구분(사용자 2026-06-20).
  function fgColor(){ return isDark() ? '#e5e7eb' : '#1f2937'; }
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
      timeScale: { borderVisible: false, timeVisible: (curInterval==='5m'||curInterval==='15m'||curInterval==='1h'), rightOffset: 7 },
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
      mainS = chart.addLineSeries({ color: fgColor(), lineWidth: 2, priceFormat: pf, lastValueVisible: true, priceLineVisible: false, title: '현재가' });
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
    var isIntraday = (typeof firstT === 'number');
    var mk = [];
    if (!isIntraday && analysisMarkers && analysisMarkers.length) {
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
      if (d.ema10)  chart.addLineSeries({ color: '#2563eb', lineWidth: 1, priceFormat: pf, lastValueVisible: false, priceLineVisible: false }).setData(zip(d.ema10));
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
      var ttDate = param.time; if (typeof ttDate === 'number') { var _dt = new Date(ttDate * 1000); ttDate = _dt.getUTCFullYear()+'-'+String(_dt.getUTCMonth()+1).padStart(2,'0')+'-'+String(_dt.getUTCDate()).padStart(2,'0')+' '+String(_dt.getUTCHours()).padStart(2,'0')+':'+String(_dt.getUTCMinutes()).padStart(2,'0'); }
      var rows = ['<div class="tt-date">' + ttDate + '</div>'];
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
      trow('종가', d.close[idx], fgColor(), prec);
      if (ind.ma) {
        trow('10 EMA', d.ema10 && d.ema10[idx], '#2563eb', prec);
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
  function rangeLabel(r){ r = r || curRange; return ({'1d':'1일','1wk':'1주일','1mo':'1개월','3mo':'3개월','6mo':'6개월','ytd':'YTD','1y':'1년','3y':'3년','5y':'5년','max':'전체'})[r] || r; }
  function intervalLabel(i){ i = i || curInterval; return ({'5m':'5분봉','15m':'15분봉','1h':'1시간봉','1d':'일봉','1wk':'주봉','1mo':'월봉'})[i] || i; }
  function buildValues(d){
    var vEl = document.getElementById('chart-values');
    if (!vEl) return;
    var dec = (d.decimals === 0) ? 0 : 2;
    var items = [];
    // 분석 가격 — 차트 안에선 가까우면 라벨이 겹쳐 가려지므로 패널에서 항상
    // 보이게 (선 색과 매칭). 현재가/시점가/진입/손절/목표.
    items.push(['현재가', (d.last_price != null ? d.last_price : lastNonNull(d.close)), fgColor(), dec]);
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
      if (d.ema10)  items.push(['10 EMA', lastNonNull(d.ema10), '#2563eb', dec]);
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
    var tval = d.times[idx] || '';
    if (typeof tval === 'number') { var dt = new Date(tval * 1000); tval = dt.getUTCFullYear()+'-'+String(dt.getUTCMonth()+1).padStart(2,'0')+'-'+String(dt.getUTCDate()).padStart(2,'0')+' '+String(dt.getUTCHours()).padStart(2,'0')+':'+String(dt.getUTCMinutes()).padStart(2,'0'); }
    var parts = ['<span class="co-date">' + tval + '</span>'];
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
    seg('종', cval, fgColor());
    // 일간 등락 — 종가 vs 직전 봉 종가.
    if (idx > 0 && d.close[idx - 1] != null && cval != null && d.close[idx - 1] > 0) {
      var ch = (cval - d.close[idx - 1]) / d.close[idx - 1] * 100;
      var col = ch > 0 ? '#26a69a' : (ch < 0 ? '#e2574c' : '#94a3b8');
      parts.push('<span class="co-seg" style="color:' + col + '">' + (ch >= 0 ? '+' : '') + ch.toFixed(2) + '%</span>');
    }
    if (ind.ma) {
      if (d.ema10 && d.ema10[idx] != null)  seg('10EMA', d.ema10[idx], '#2563eb');
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
    function fmtT(t){ if(typeof t!=='number') return t; var dt=new Date(t*1000); return dt.getUTCFullYear()+'-'+String(dt.getUTCMonth()+1).padStart(2,'0')+'-'+String(dt.getUTCDate()).padStart(2,'0')+' '+String(dt.getUTCHours()).padStart(2,'0')+':'+String(dt.getUTCMinutes()).padStart(2,'0'); }
    var isIntrd = (n > 0 && typeof d.times[0] === 'number');
    var src = isIntrd ? '네이버 · Yahoo Finance' : 'Yahoo Finance';
    cEl.textContent = rangeLabel(curRange) + ' · ' + intervalLabel(curInterval) + ' · '
                    + n + '개 봉 · ' + fmtT(first) + ' → ' + fmtT(last) + ' · ' + src;
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
        if (j && j.ok && j.chart) {
          status('');
          // 서버가 반환한 실제 봉/범위로 동기화 — 서버가 요청 range/interval 을
          // 화이트리스트 미일치로 보정(예: 미배포 시 1주일→1년)했을 때 값 패널·
          // 버튼이 '실제 데이터' 를 정직하게 반영하게(기간 % 오라벨 방지).
          if (j.chart.period) curRange = j.chart.period;
          if (j.chart.interval) curInterval = j.chart.interval;
          render(j.chart);
          setActive();
        }
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
      else if (kind === 'range') {
        curRange = val;
        var autoMap = {'1d':'5m','1wk':'15m','1mo':'1h'};
        if (autoMap[val]) curInterval = autoMap[val];
        else if (['5m','15m','1h'].indexOf(curInterval) >= 0) curInterval = '1d';
      }
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

    # ⑤⑥ 재무제표 + 동종비교 (yfinance, 독립 키 si["financials"]/si["peer_comps"])
    # — 둘 다 느린 yfinance 호출이라 병렬 실행해 종목분석 지연 단축(사용자
    # 2026-06-10 '종목지연 다시봐'). 키가 달라 race 없음, 각 try/except 로 격리.
    def _e_financials():
        if not si.get("financials"):
            try:
                import yfinance as yf
                from bot.stock_snapshot import _collect_financials
                _collect_financials(yf.Ticker(ticker), si)
            except Exception as exc:
                log.debug("_ensure_detail_enrichment: financials %s: %s", ticker, exc)

    def _e_peers():
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

    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=2) as _pool:
            for _fut in (_pool.submit(_e_financials), _pool.submit(_e_peers)):
                _fut.result()
    except Exception:
        _e_financials()
        _e_peers()  # 폴백: 순차

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


def _fmt_over_line(q: dict, csym: str, pdec: int, src: str = "시간외") -> str:
    """Naver overMarketPriceInfo → 상세 연장거래 라인 '🌙 장후 · 정규장 종가
    ₩123,200 · NXT ₩135,600 (+10.06%)' (사용자 2026-06-16, 네이버페이 미러:
    메인=정규장종가, 연장가=별도). 메인 현재가/차트는 정규장 종가이고 이 라인이
    연장(KR=NXT·US=프리/애프터) 가격 + 정규장 대비 변동을 분리 표시. src =
    소스명(KR='NXT'(넥스트레이드) / US/JP/HK='시간외' — NXT≠시간외라 시장별,
    사용자 2026-06-16). over_session/over_price 없으면(정규장·비지원) "" → 숨김."""
    if not isinstance(q, dict):
        return ""
    sess = q.get("over_session") or ""
    op = q.get("over_price")
    rc = q.get("reg_close")
    if not sess or not op:
        return ""
    label = "장전" if "PRE" in sess else "장후"
    parts = [f"🌙 {label}"]
    if rc is not None:
        parts.append(f"정규장 종가 {csym}{_fmt_num(rc, decimals=pdec)}")
    seg = f"{src} {csym}{_fmt_num(op, decimals=pdec)}"
    if rc and rc > 0:                       # 정규장 종가 대비 순수 연장 변동(보드와 동일식)
        mv = (op / rc - 1) * 100
        seg += f" ({'+' if mv >= 0 else ''}{mv:.2f}%)"
    parts.append(seg)
    return " · ".join(parts)


def build_live_quote(ticker: str, full: bool = False,
                     force_fresh: bool = False) -> dict | None:
    """Fresh live-quote payload for the detail-page overlay (numbers only).

    force_fresh=True: 강제 신선(수동 🔄 / stale 백그라운드 force=1) — 120초 스냅샷
    캐시 우회해 yfinance 새로 수집. 기본 False 면 캐시 재사용(cold 1장 중복 제거).


    LIGHT (full=False): one yfinance ``.info`` call → price-derived
    multiples + consensus + 52주 + 이평. KR (.KS/.KQ) takes a Naver-first
    real-time 현재가 / PER / PBR / 시가총액 / 52주 override — 네이버 폴링은
    무키이고 데이터센터 IP 차단이 없으며 yfinance 의 EOD-stale KR 시세보다
    신선하다 (PER/PBR 은 현재가/EPS·현재가/BPS 로 장중 재계산). ~0.5-1s,
    intraday-fresh, ₩0.

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
            fresh = collect_stock_snapshot(ticker, use_cache=not force_fresh)
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

    # ── LIGHT — price-derived numbers, one .info call (+ KR 네이버) ───────
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
    # 야후 .info throttle 시에도 블랭크 금지 — 아래 history 현재가 폴백으로 진행
    # (사용자 2026-06-14 '야후가 짤라서 종목검색 아무것도 못함'). 진짜 데이터가
    # 하나도 없으면 끝의 `if not fmt: return None` 가 잡음.
    currency = info.get("currency")
    if not currency:
        _SUF_CCY = {".KS": "KRW", ".KQ": "KRW", ".T": "JPY", ".TW": "TWD",
                    ".HK": "HKD", ".SS": "CNY", ".SZ": "CNY", ".L": "GBp"}
        currency = next((c for s, c in _SUF_CCY.items()
                         if ticker.upper().endswith(s)), "USD")
    csym = _currency_sym(currency)
    _0dec = ("KRW", "JPY", "TWD", "HKD")
    pdec = 0 if currency in _0dec else 2
    over_line = ""    # 미국 시간외(장전/장후) 라인 (Naver 식) — wq 에서 채움

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
        # KR 현재가·시총 = Naver 국내 실시간 (키 불필요·VM 차단 없음·장중 라이브).
        # 사용자 2026-06-15 '한국도 네이버로, KIS 말고' — 상세/차트 가격에서 KIS
        # 의존 제거(Naver 만). PER/PBR/EPS/52주 는 yfinance .info(vals) 사용.
        try:
            from bot.naver_quote import fetch_kr_quote
            nq = fetch_kr_quote(ticker)
            if nq and nq.get("price"):
                price = nq["price"]
                source, delayed = "네이버 실시간", False
                if nq.get("mcap"):
                    mcap = nq["mcap"]
                over_line = _fmt_over_line(nq, csym, pdec, "NXT")  # KR=NXT(넥스트레이드)
        except Exception as exc:
            log.debug("build_live_quote: Naver KR quote skipped for %s: %s", ticker, exc)
    else:
        # 비-KR 현재가·시총 = 시장별 라이브(사용자 2026-06-15 '다 네이버'): US/JP/HK/
        # CN → 네이버 해외(worldstock), TW → 대만거래소(TWSE) 공식. 키 불필요·VM
        # 차단 없음. 실패/미지원 시장은 아래 yfinance 폴백 유지.
        try:
            from bot.market import detect_market
            _mkt = detect_market(ticker)
            wq = None
            if _mkt == "TW":
                from bot.tw_quote import fetch_tw_quote
                wq = fetch_tw_quote(ticker)
            elif _mkt in ("US", "JP", "HK", "CN_A"):
                from bot.world_quote import fetch_world_quote
                wq = fetch_world_quote(ticker)
            if wq and wq.get("price"):
                price = wq["price"]
                source, delayed = wq.get("source", "실시간"), False
                if wq.get("mcap"):
                    mcap = wq["mcap"]
                # 시간외(장전/장후) 라인 — US/JP/HK/CN 해외 공용 (사용자 2026-06-16
                # '대만제외 다 적용'). over_session 없으면 "" (JP/HK/CN 은 연장거래
                # 미지원이라 보통 빈 값 → :empty 숨김).
                over_line = _fmt_over_line(wq, csym, pdec, "시간외")  # US=프리/애프터(NXT 아님)
        except Exception as exc:
            log.debug("build_live_quote: world/tw quote skipped for %s: %s", ticker, exc)

    if not price:
        # 야후 .info(+KR 네이버) 둘 다 비면 history(관대 chart API)로 현재가 폴백 —
        # .info throttle(quote API 차단) 중에도 검색이 최소 현재가는 보이게(사용자
        # 2026-06-14 '야후가 짤라서 아무것도 못함'). /health 가 history(batch) OK
        # 확인 — quote API 와 별개로 살아있음. 전 시장 universal. 5d 마지막 종가
        # = 직전 거래일 종가(지연 표기). KLAC류 분할 글리치는 75% 밖이면 제외.
        try:
            _h = yf.Ticker(ticker).history(period="5d")
            if _h is not None and len(_h) and "Close" in _h:
                _cl = _h["Close"].dropna()
                if len(_cl):
                    _hc = float(_cl.iloc[-1])
                    _pc = float(_cl.iloc[-2]) if len(_cl) >= 2 else _hc
                    if not (_pc > 0 and abs(_hc / _pc - 1) > 0.75):   # 글리치 가드
                        price = _hc
                        if source == "yfinance":
                            source = "yfinance 종가"
        except Exception as _exc:
            log.debug("build_live_quote: history price fallback %s: %s", ticker, _exc)

    # PER/PBR 라이브 재계산 — 현재가가 라이브(네이버 국내·해외·TWSE)이고 yfinance
    # EPS/BPS(장중 불변)가 있으면 PER=현재가/EPS · PBR=현재가/BPS 로 재산출해 장중
    # 가격 반영(사용자 2026-06-15 'PER/PBR 더 실시간'). 적자(eps≤0)·무BPS 는 yfinance
    # 값 유지. 52주 신고/신저도 장중 라이브가 극값 돌파 시 갱신. 전 시장 universal.
    if price and not delayed:
        _eps = vals.get("trailingEps")
        if isinstance(_eps, (int, float)) and _eps > 0:
            vals["trailingPE"] = price / _eps
        _bps = vals.get("bookValue")
        if isinstance(_bps, (int, float)) and _bps > 0:
            vals["priceToBook"] = price / _bps
        _h52, _l52 = vals.get("fiftyTwoWeekHigh"), vals.get("fiftyTwoWeekLow")
        if isinstance(_h52, (int, float)) and price > _h52:
            vals["fiftyTwoWeekHigh"] = price
        if isinstance(_l52, (int, float)) and 0 < price < _l52:
            vals["fiftyTwoWeekLow"] = price

    # Pre-format every field with the SAME helpers the renderer uses so
    # the client just drops strings into [data-q] cells (no JS-side
    # formatting drift). Skip None so a live miss never blanks a value
    # the stored snapshot already had.
    fmt: dict = {}
    if price:
        fmt["price"] = _fmt_num(price, decimals=pdec, prefix=csym)
    fmt["over_line"] = over_line   # 미국 시간외 라인(없으면 "" → :empty 로 숨김)
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


# 밴드차트(PER/PBR) 클라이언트 렌더 — FnGuide BandChart 충실 재현, 외부 라이브러리 0.
# 탭 활성화 시 /api/band 로 lazy fetch → FnGuide 가 준 멀티플·밴드선·주가(전망 포함)
# 시계열(x=ms epoch, y=값/미래 price=null)을 순수 SVG 로 그린다.
_BAND_JS = r"""
(function(){
  var pane=document.getElementById('si-bandchart'); if(!pane) return;
  var CD=null, fetching=false;
  function base(){ return (typeof NOAH_BASE!=='undefined')?NOAH_BASE:'../'; }
  function tkr(){ return (typeof NOAH_TICKER!=='undefined')?NOAH_TICKER:''; }
  function fmt0(v){ if(v==null||!isFinite(v)) return '—'; return Math.round(v).toLocaleString(); }
  function ymd(ms){ var d=new Date(ms); return d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2); }
  var BANDC=['#ef8a33','#7e57c2','#9aa056','#e2574c'];   // VAL1 최고 … VAL4 최저
  var NAMES=['최고','중상','중하','최저'];
  // obj = {mult:[v1..v4], price:[[ms,y|null]], bands:[[[ms,y]]×4]}
  function drawBand(holderId, lgId, obj, csym){
    var holder=document.getElementById(holderId), lg=document.getElementById(lgId);
    if(!holder) return;
    if(!obj||!obj.price||!obj.price.length){
      holder.innerHTML='<div style="font-size:12px;color:var(--fg-soft)">데이터 없음</div>'; if(lg)lg.innerHTML=''; return; }
    var price=obj.price, bands=obj.bands||[], mult=obj.mult||[];
    var W=holder.clientWidth||340, H=240, PADL=58, PADR=8, PADT=8, PADB=22;
    var minX=Infinity,maxX=-Infinity, ymin=Infinity,ymax=-Infinity;
    function scanX(a){ for(var i=0;i<a.length;i++){ var x=a[i][0]; if(x!=null){ if(x<minX)minX=x; if(x>maxX)maxX=x; } } }
    // FnGuide 는 적자(EPS≤0)·무의미 구간의 밴드/멀티플을 0 으로 채워 보낸다 →
    // 0 = '그 시기 PER 정의 불가'라 선을 ₩0 으로 잇지 않고 갭 처리(축 범위에서도 제외).
    function scanY(a){ for(var i=0;i<a.length;i++){ var y=a[i][1]; if(y!=null&&isFinite(y)&&y>0){ if(y<ymin)ymin=y; if(y>ymax)ymax=y; } } }
    scanX(price); scanY(price);
    for(var b=0;b<bands.length;b++){ scanX(bands[b]); scanY(bands[b]); }
    if(!isFinite(minX)||!isFinite(ymin)||maxX<=minX||ymax<=ymin){ holder.innerHTML=''; return; }
    var pd=(ymax-ymin)*0.05; ymin-=pd; ymax+=pd; if(ymin<0)ymin=0;
    function X(ms){ return PADL+(W-PADL-PADR)*(ms-minX)/(maxX-minX); }
    function Y(v){ return PADT+(H-PADT-PADB)*(1-(v-ymin)/(ymax-ymin)); }
    function poly(a){ var d='',on=false;
      for(var i=0;i<a.length;i++){ if(a[i][0]==null||a[i][1]==null||a[i][1]<=0){ on=false; continue; }
        d+=(on?'L':'M')+X(a[i][0]).toFixed(1)+' '+Y(a[i][1]).toFixed(1)+' '; on=true; }
      return d; }
    var svg='<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none" style="overflow:visible">';
    for(var g=0;g<=4;g++){ var gv=ymin+(ymax-ymin)*g/4, gy=Y(gv);
      svg+='<line x1="'+PADL+'" y1="'+gy.toFixed(1)+'" x2="'+(W-PADR)+'" y2="'+gy.toFixed(1)+'" stroke="var(--border-soft,#333)" stroke-width="0.5"/>';
      svg+='<text x="'+(PADL-4)+'" y="'+(gy+3).toFixed(1)+'" text-anchor="end" font-size="9" fill="var(--fg-soft,#999)">'+csym+fmt0(gv)+'</text>'; }
    // 현재(price 마지막 비-null) 세로 점선 — 오른쪽은 전망 구간
    var lastReal=null; for(var i=price.length-1;i>=0;i--){ if(price[i][1]!=null){ lastReal=price[i][0]; break; } }
    if(lastReal!=null && lastReal<maxX){ var lx=X(lastReal).toFixed(1);
      svg+='<line x1="'+lx+'" y1="'+PADT+'" x2="'+lx+'" y2="'+(H-PADB)+'" stroke="var(--fg-soft,#888)" stroke-width="0.6" stroke-dasharray="3 3"/>'; }
    for(var k=0;k<bands.length;k++){ if(mult[k]==null||mult[k]<=0) continue;   // 0.0x 등 무의미 밴드 제외
      var d=poly(bands[k]);
      if(d) svg+='<path d="'+d+'" fill="none" stroke="'+BANDC[k]+'" stroke-width="1.2" opacity="0.85"/>'; }
    svg+='<path d="'+poly(price)+'" fill="none" stroke="#2f80ed" stroke-width="2"/>';
    svg+='<text x="'+PADL+'" y="'+(H-6)+'" font-size="9" fill="var(--fg-soft,#999)">'+ymd(minX)+'</text>';
    svg+='<text x="'+(W-PADR)+'" y="'+(H-6)+'" text-anchor="end" font-size="9" fill="var(--fg-soft,#999)">'+ymd(maxX)+'</text>';
    svg+='</svg>';
    holder.innerHTML=svg;
    if(lg){ var h='<span style="color:#2f80ed;font-weight:600">― 주가</span> ';
      for(var k2=0;k2<mult.length;k2++){ if(mult[k2]==null||mult[k2]<=0) continue;
        h+='<span style="color:'+BANDC[k2]+'">― '+mult[k2].toFixed(1)+'x('+NAMES[k2]+')</span> '; }
      lg.innerHTML=h; }
  }
  function draw(){ if(!CD) return;
    drawBand('si-band-per','si-band-per-lg',CD.per,'₩');
    drawBand('si-band-pbr','si-band-pbr-lg',CD.pbr,'₩'); }
  function load(){ if(CD||fetching) return; fetching=true;
    var st=document.getElementById('si-band-status');
    fetch(base()+'api/band?ticker='+encodeURIComponent(tkr()),{cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(j){ fetching=false;
        if(!j||!j.ok||!j.band){ if(st)st.textContent='밴드차트 데이터가 없습니다(커버리지 없음).'; return; }
        CD=j.band; if(st)st.style.display='none'; draw(); })
      .catch(function(){ fetching=false; if(st)st.textContent='차트 로딩 실패.'; }); }
  document.addEventListener('click',function(e){
    var b=e.target.closest&&e.target.closest('.si-tab[data-pane="si-bandchart"]');
    if(b) setTimeout(load,30); });
  if(pane.classList.contains('active')) setTimeout(load,60);
  var rT; window.addEventListener('resize',function(){ if(CD){ clearTimeout(rT); rT=setTimeout(draw,150); } });
})();
"""


# 기술 분석 탭 — 탭 활성화 시 /api/technical 로 지표 lazy fetch(무과금·즉시 표),
# '실행' 버튼 클릭 시에만 &run=1 단일 LLM 호출 → 강세/약세·축별판정·시나리오 렌더.
_TECHNICAL_JS = r"""
(function(){
  // ⚠️ lookup 지연로딩은 이 스크립트가 든 other_panes 를 core/full 2회 주입(inject
  // 가 매번 재실행) → 중복 시 document 클릭 리스너 2개 = '실행' 1클릭이 &run=1 을
  // 2회 발사(첫 실행분 캐시 전이라 Gemini 2중 과금). window 플래그로 1회만 배선.
  // 상태(지표·토론)도 window 에 둬 DOM 교체(core→full) 후에도 재렌더 가능.
  if(window.__noahTechWired) return; window.__noahTechWired=1;
  function base(){ return (typeof NOAH_BASE!=='undefined')?NOAH_BASE:'../'; }
  function tkr(){ return (typeof NOAH_TICKER!=='undefined')?NOAH_TICKER:''; }
  function esc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':String(s)); return d.innerHTML; }
  function fmt(v){ if(v==null||v===''||(typeof v==='number'&&!isFinite(v))) return '—';
    if(typeof v==='number') return v.toLocaleString(undefined,{maximumFractionDigits:3}); return esc(v); }
  // ── 지표 표 ──
  var ROWS=[
    ['종가','close'],['EMA10','ema10'],['SMA50','sma50'],['SMA200','sma200'],
    ['RSI14','rsi14'],['MACD','macd'],['MACD 시그널','macd_signal'],['MACD 히스토그램','macd_hist'],
    ['볼린저 위치(%)','boll_pos_pct'],['ATR14','atr14'],['ATR(%)','atr_pct'],
    ['VWMA20','vwma20'],['거래량/20일평균','vol_ratio'],['60일 고가','high60'],['60일 저가','low60']
  ];
  function renderInd(ind){
    var h='<table class="si-table"><thead><tr><th>지표</th><th class="num">값</th></tr></thead><tbody>';
    for(var i=0;i<ROWS.length;i++){ h+='<tr><td>'+ROWS[i][0]+'</td><td class="num">'+fmt(ind[ROWS[i][1]])+'</td></tr>'; }
    h+='</tbody></table>';
    var as=ind.asof?('<div style="font-size:11px;color:var(--fg-soft);margin-top:6px">기준일 '+esc(ind.asof)+'</div>'):'';
    document.getElementById('si-tech-ind').innerHTML=h+as;
  }
  function loadInd(){
    var st=document.getElementById('si-tech-ind-status');
    if(window.__noahTechIND){   // 캐시 적중 — DOM 교체(lookup core→full) 후 재렌더
      renderInd(window.__noahTechIND); if(st)st.style.display='none';
      if(window.__noahTechDebate) renderDebate(window.__noahTechDebate); return; }
    if(window.__noahTechFetching) return; window.__noahTechFetching=true;
    fetch(base()+'api/technical?ticker='+encodeURIComponent(tkr()),{cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(j){ window.__noahTechFetching=false;
        if(!j||!j.ok||!j.indicators){ if(st)st.textContent='지표 데이터가 없습니다(상장 이력 부족 또는 데이터 오프라인).'; return; }
        window.__noahTechIND=j.indicators; if(st)st.style.display='none'; renderInd(j.indicators);
        if(j.debate&&j.debate.ok){ window.__noahTechDebate=j.debate; renderDebate(j.debate); } })
      .catch(function(){ window.__noahTechFetching=false; if(st)st.textContent='지표 로딩 실패.'; });
  }
  // ── 토론 렌더 ──
  var VC={'강한 상승 우위':'#1b8f5a','상승 우위':'#26a69a','중립':'var(--fg-soft)',
          '하락 우위':'#e2574c','강한 하락 우위':'#c0392b'};
  function bar(v,color){ v=Math.max(0,Math.min(100,Number(v)||0));
    return '<div style="background:var(--border);border-radius:4px;height:8px;overflow:hidden">'
      +'<div style="width:'+v+'%;height:100%;background:'+(color||'var(--accent)')+'"></div></div>'; }
  function list(arr,color){ if(!arr||!arr.length) return '<div style="color:var(--fg-soft);font-size:12px">—</div>';
    var h='<ul style="margin:4px 0 0;padding-left:18px;font-size:13px;line-height:1.6">';
    for(var i=0;i<arr.length;i++) h+='<li>'+esc(arr[i])+'</li>'; return h+'</ul>'; }
  function renderDebate(d){
    var box=document.getElementById('si-tech-debate'); if(!box) return;
    var vc=VC[d.verdict]||'var(--fg)';
    var h='<div style="border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:14px">'
      +'<div style="font-size:18px;font-weight:700;color:'+vc+'">'+esc(d.verdict||'—')+'</div>';
    if(d.cached) h+='<div style="font-size:11px;color:var(--fg-soft);margin-top:2px">캐시(오늘 실행분 · 무과금)</div>';
    h+='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:10px;font-size:12px">';
    function metric(lbl,v,c){ return '<div><div style="color:var(--fg-soft);margin-bottom:3px">'+lbl+' '+(v==null?'':Math.round(v))+'</div>'+bar(v,c)+'</div>'; }
    h+=metric('점수',d.score,vc)+metric('신뢰도',d.confidence)+metric('합의도',d.consensus);
    h+='</div></div>';
    // 강세/약세
    h+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">'
      +'<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px">'
      +'<div style="font-weight:700;color:#26a69a">강세 연구원</div>'+list(d.bull)+'</div>'
      +'<div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px">'
      +'<div style="font-weight:700;color:#e2574c">약세 연구원</div>'+list(d.bear)+'</div></div>';
    // 축별 판정표
    if(d.axes&&d.axes.length){
      function cell(t){ t=(t==null?'':String(t)).trim(); return t?esc(t):'<span style="color:var(--fg-soft)">—</span>'; }
      function vcol(t){ t=t||''; return /강세/.test(t)?'#26a69a':/약세/.test(t)?'#e2574c':'var(--fg-soft)'; }
      h+='<div class="si-section-title" style="font-size:13px">축별 판정</div>'
        +'<table class="si-table"><thead><tr><th>축</th><th>강세 논거</th><th>약세 논거</th><th class="num">판정</th></tr></thead><tbody>';
      for(var i=0;i<d.axes.length;i++){ var a=d.axes[i];
        var av=(a.axis_verdict||a.verdict||'').toString().trim();
        var vb=av?('<span style="display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap;color:'+vcol(av)+';border:1px solid '+vcol(av)+'">'+esc(av)+'</span>'):'—';
        h+='<tr><td style="white-space:nowrap">'+esc(a.name)+'</td><td style="font-size:12px">'+cell(a.bull)+'</td>'
          +'<td style="font-size:12px">'+cell(a.bear)+'</td><td class="num">'+vb+'</td></tr>'; }
      h+='</tbody></table>';
    }
    // 시나리오
    if(d.scenarios){ var s=d.scenarios;
      h+='<div class="si-section-title" style="font-size:13px;margin-top:12px">조건부 시나리오</div>'
        +'<table class="si-table"><tbody>'
        +'<tr><td style="white-space:nowrap;color:#26a69a">📈 추세 강화</td><td style="font-size:12px">'+esc(s.up)+'</td></tr>'
        +'<tr><td style="white-space:nowrap;color:var(--fg-soft)">↔️ 통상 범위</td><td style="font-size:12px">'+esc(s.range)+'</td></tr>'
        +'<tr><td style="white-space:nowrap;color:#e2574c">📉 추세 약화</td><td style="font-size:12px">'+esc(s.down)+'</td></tr>'
        +'</tbody></table>'; }
    box.innerHTML=h;
  }
  function runDebate(){ if(window.__noahTechBusy) return;
    if(!window.__noahTechIND){ var s0=document.getElementById('si-tech-debate-status'); if(s0)s0.textContent='지표를 먼저 불러와야 합니다.'; return; }
    window.__noahTechBusy=true;
    var btn=document.getElementById('si-tech-run'), st=document.getElementById('si-tech-debate-status');
    if(btn){ btn.disabled=true; btn.style.opacity='0.6'; btn.style.cursor='default'; }
    if(st)st.textContent='분석 중… (수 초 소요)';
    fetch(base()+'api/technical?ticker='+encodeURIComponent(tkr())+'&run=1',{cache:'no-store'})
      .then(function(r){return r.json();})
      .then(function(j){ window.__noahTechBusy=false; if(btn){ btn.disabled=false; btn.style.opacity=''; btn.style.cursor='pointer'; }
        if(!j||!j.ok||!j.debate||!j.debate.ok){ if(st)st.textContent=(j&&j.debate&&j.debate.error)?('실행 실패: '+esc(j.debate.error)):'실행 실패.'; return; }
        window.__noahTechDebate=j.debate; if(st)st.textContent=''; renderDebate(j.debate); })
      .catch(function(){ window.__noahTechBusy=false; if(btn){ btn.disabled=false; btn.style.opacity=''; btn.style.cursor='pointer'; } if(st)st.textContent='실행 실패(네트워크).'; });
  }
  document.addEventListener('click',function(e){
    var b=e.target.closest&&e.target.closest('.si-tab[data-pane="si-technical"]');
    if(b){ setTimeout(loadInd,30); return; }
    if(e.target&&e.target.id==='si-tech-run') runDebate();
  });
  var p0=document.getElementById('si-technical');   // 종합이 기본 — 보통 미발화
  if(p0&&p0.classList.contains('active')) setTimeout(loadInd,60);
})();
"""


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

    # 글리치 보정 주석 (KLAC 2026-06-12 — stock_snapshot 가드가 직전 종가로
    # 교체한 경우 사용자에게 보정 사실 표기)
    _glitch = si.get("price_glitch_note") or ""
    _glitch_html = (f'<div style="grid-column:1/-1;font-size:11px;'
                    f'color:#f5a623">⚠️ {esc(_glitch)}</div>' if _glitch else "")
    header = f"""<div class="si-header">
  <div class="si-card"><span class="si-label">현재가</span>
    <span class="si-value" data-q="price">{esc(price_str)}</span></div>
  <div class="si-card"><span class="si-label">시가총액</span>
    <span class="si-value" data-q="mcap">{esc(mcap_str)}</span></div>
  {_glitch_html}
  <div class="si-card"><span class="si-label">발행주식수</span>
    <span class="si-value">{esc(shares_str)}</span></div>
  <div class="si-card"><span class="si-label">다음 실적</span>
    <span class="si-value">{esc(ne_label)}</span>
    <span class="si-sub">{esc(ne_sub)}</span></div>
</div>
<style>.si-over:empty{{display:none}}</style>
<div class="si-over" data-q="over_line" style="margin:-4px 0 10px;font-size:13px;color:#c77d2e;font-weight:600"></div>
<div class="si-quote-status" style="margin:-8px 0 16px;font-size:11px;color:var(--fg-soft)">
  <span id="q-badge" style="font-weight:600"></span>
  <span id="q-dot" class="q-dot q-dot-load" title="데이터 로딩 중…"></span>
  <button id="q-refresh" style="background:none;border:none;cursor:pointer;font-size:14px;margin-left:2px;padding:2px 4px;vertical-align:middle" title="데이터 새로고침">🔄</button>
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

    # ── 밴드차트 탭 (PER/PBR — FnGuide 밴드차트 충실 재현, KR 전용) ──
    # 데이터는 탭 활성화 시 /api/band 로 lazy fetch(FnGuide BandChart3.aspx, 12h 캐시).
    # 자체 EPS/BPS 보간 근사 대신 FnGuide 가 산출한 밴드선·멀티플·전망(미래)을 그대로.
    # 데이터소스가 KR 전용이라 탭도 KR 한정 — 커버리지 없으면 패널이 '데이터 없음'.
    band_tab = ('  <button type="button" class="si-tab" data-pane="si-bandchart">밴드차트</button>\n'
                if is_kr else "")

    # ── tab navigation ──────────────────────────────────────────
    # 공시 버튼은 항상 노출 (사용자 2026-06-16 A2): 옛 분석은 정적 저장분에
    # 공시가 없어 has_disclosures=False 였고, 그러면 버튼이 안 떠 라이브
    # 오버레이(/api/quote?full=1)가 si-disclosures 를 채워도 사용자가 탭에
    # 접근할 수 없었다. 모든 시장에 실제 공시 소스(DART/EDINET/MOPS/AKShare/
    # SEC)가 있으므로 flow/financials/peers 처럼 항상 노출 → 오버레이가 채움.
    disclosure_tab = '  <button type="button" class="si-tab" data-pane="si-disclosures">공시</button>\n'
    # Tab buttons for live-fetchable panes are always shown — the JS
    # overlay (/api/quote?full=1) fills them after page load.  During
    # batch regen the pane content is empty, but the button + placeholder
    # div must exist so getElementById() in the overlay JS finds them.
    has_flow = is_kr or is_cn or is_jp or is_tw or is_us  # any equity market
    flow_tab = '  <button type="button" class="si-tab" data-pane="si-flow">수급</button>\n' if has_flow else ""
    has_risk = bool(kr.get("lockup_releases") or kr.get("dilution_events") or kr.get("market_alert")) or bool(is_cn and mkt.get("risk_status"))
    risk_tab = '  <button type="button" class="si-tab" data-pane="si-risk">리스크</button>\n' if has_risk else ""
    has_financials = True  # enrichment overlay always fills this
    financials_tab = '  <button type="button" class="si-tab" data-pane="si-financials">재무제표</button>\n'
    has_peers = True  # enrichment overlay always fills this
    peers_tab = '  <button type="button" class="si-tab" data-pane="si-peers">동종비교</button>\n'
    tabs_html = f"""<div class="si-tabs" id="si-tab-bar">
  <button type="button" class="si-tab active" data-pane="si-overview">종합</button>
  <button type="button" class="si-tab" data-pane="si-company">기업</button>
  <button type="button" class="si-tab" data-pane="si-consensus">컨센서스</button>
  <button type="button" class="si-tab" data-pane="si-valuation">밸류에이션</button>
{band_tab}{financials_tab}{peers_tab}  <button type="button" class="si-tab" data-pane="si-earnings">실적</button>
  <button type="button" class="si-tab" data-pane="si-research">리서치</button>
  <button type="button" class="si-tab" data-pane="si-holders">주주</button>
{flow_tab}{disclosure_tab}{risk_tab}  <button type="button" class="si-tab" data-pane="si-news">뉴스</button>
  <button type="button" class="si-tab" data-pane="si-timeline">타임라인</button>
  <button type="button" class="si-tab" data-pane="si-technical">기술 분석</button>
</div>"""

    # ── 기업 정보 pane ──────────────────────────────────────────
    from bot.translate import (sector_kr, industry_kr, country_kr,
                               grade_kr, action_kr,
                               translate_description_kr, translate_news_titles_kr)
    desc = si.get("description", "")
    # 비-KR: 네이버 한국어 기업개요(worldstock summary) 우선 — ₩0·번역 불요(사용자
    # 2026-06-16). 실패/KR 은 yfinance 영문 + Gemini 번역 폴백. 디스크 캐시(30일)라
    # bulk regen 부하 무(종목당 1회). graceful.
    _nv_desc = None
    if not is_kr:
        try:
            from bot.naver_overview import fetch_naver_overview
            _nv_desc = fetch_naver_overview(ticker)
        except Exception:
            _nv_desc = None
    if _nv_desc:
        desc = _nv_desc                        # 네이버 한국어 — 번역 불요
    elif desc:
        desc = translate_description_kr(desc)   # 폴백: 영문 → Gemini
    # 네이버 summary 는 <br>→\n 변환돼 있어 문단 보존 위해 esc 후 <br> 복원.
    desc_html = (f'<div class="si-desc">{esc(desc[:2000]).replace(chr(10), "<br>")}</div>'
                 if desc else "")

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

    _src_foot = '<div style="font-size:11px;color:var(--fg-soft);margin-top:8px;text-align:right">'

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
  {_src_foot}출처: {"개요 네이버 · 그 외 yfinance" if _nv_desc else "yfinance"}</div>
</div>"""

    # ── 컨센서스 pane ───────────────────────────────────────────
    # 실적 서프라이즈(KR) — WISEreport 어닝서프라이즈(영업이익·당기순이익 ×
    # 컨센서스/잠정치/Surprise/전년동기). mkt.earnings_surprise = 스냅샷 수집분.
    def _es_table(es: dict) -> str:
        periods = es.get("periods") or []
        if not periods:
            return ""
        ncol = len(periods)
        def _amt(v):   # 억원
            return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"
        def _pct(v):
            if not isinstance(v, (int, float)):
                return "—"
            c = "#26a69a" if v >= 0 else "#e2574c"
            return f'<span style="color:{c}">{v:+.1f}%</span>'
        def _row(label, vals, fmt, top=False):
            cells = "".join(f'<td class="num">{fmt(vals[i] if i < len(vals) else None)}</td>'
                            for i in range(ncol))
            edge = ' style="border-top:1px solid var(--border-soft)"' if top else ""
            return f'<tr{edge}><td>{esc(label)}</td>{cells}</tr>\n'
        ann = es.get("announce") or []
        ann_cells = "".join(f'<td class="num">{esc(str(ann[i])) if i < len(ann) else "—"}</td>'
                            for i in range(ncol))
        hdr = "".join(f'<th class="num">{esc(p)}</th>' for p in periods)
        op, ni = es.get("op", {}), es.get("ni", {})
        return f"""<div class="si-section" style="margin-top:16px">
    <div class="si-section-title">실적 서프라이즈 <span style="font-weight:400;color:var(--fg-soft);font-size:11px">(영업이익·당기순이익 · 단위 억원, %)</span></div>
    <table class="si-table"><thead><tr><th>항목</th>{hdr}</tr></thead><tbody>
      {_row("영업이익 컨센서스", op.get("consensus", []), _amt, top=True)}{_row("영업이익 잠정치", op.get("actual", []), _amt)}{_row("영업이익 Surprise", op.get("surprise", []), _pct)}{_row("영업이익 전년동기대비", op.get("yoy", []), _pct)}
      {_row("당기순이익 컨센서스", ni.get("consensus", []), _amt, top=True)}{_row("당기순이익 잠정치", ni.get("actual", []), _amt)}{_row("당기순이익 Surprise", ni.get("surprise", []), _pct)}{_row("당기순이익 전년동기대비", ni.get("yoy", []), _pct)}
      <tr style="border-top:1px solid var(--border-soft)"><td>잠정치 발표(예정)일/회계기준</td>{ann_cells}</tr>
    </tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">컨센서스·잠정치=억원 · Surprise·전년동기대비=% · 잠정치 ●=지배주주 기준</div>
  </div>"""

    surprise_html = ""
    if is_kr:
        _es = mkt.get("earnings_surprise")
        if _es:
            surprise_html = _es_table(_es)

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
    <div class="si-section-title">보조 컨센서스</div>
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
  {surprise_html}
  {_src_foot}출처: {"yfinance · FnGuide · Naver · 한경" if is_kr else "yfinance · Kabutan" if is_jp else "yfinance · 鉅亨網" if is_tw else "yfinance"}</div>
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

    # B3/B4 (2026-06-16): CN A주(AKShare)·TW(FinMind) 멀티플 폴백 — yfinance
    # PER/PBR(/PSR) None 시 현지 소스 값으로 채움. 폴백값은 라이브 [data-q]
    # 오버레이가 yfinance None 으로 덮어쓰지 않도록 data-q 를 비워 정적 표시.
    _alt_val: dict = {}
    if is_cn:
        _alt_val = mkt.get("valuation") or {}          # {per, pbr, psr}
    elif is_tw:
        _pp = [r for r in (mkt.get("per_pbr") or []) if isinstance(r, dict)]
        if _pp:
            _lt = max(_pp, key=lambda r: r.get("date", ""))   # 최신 일자
            _alt_val = {"per": _lt.get("PER"), "pbr": _lt.get("PBR")}
    def _mrow(label, yf_key, alt_key, suffix="x"):
        yv = si.get(yf_key)
        if yv is not None:
            return _val_row(label, yv, suffix, yf_key)
        av = _alt_val.get(alt_key)
        if av is not None:
            return _val_row(label, av, suffix, "")   # 정적(현지 소스 폴백)
        return _val_row(label, None, suffix, yf_key)

    val_multiples = ""
    val_multiples += _mrow("PER (후행)", "trailingPE", "per")
    val_multiples += _val_row("PER (선행)", si.get("forwardPE"), "x", "forwardPE")
    val_multiples += _mrow("PBR (주가순자산)", "priceToBook", "pbr")
    val_multiples += _mrow("PSR (주가매출)", "priceToSalesTrailing12Months", "psr")
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

    # 최근 실적(EPS 예상/실제/서프라이즈) 표는 전 시장 yfinance earnings_history.
    # TW 월매출만 FinMind/TWSE(자체 풋노트 보유). 옛 코드가 KR=DART·TW=MOPS·
    # CN=AKShare 로 표기했으나 실제 소스와 불일치 → yfinance 로 정정(사용자 2026-06-16).
    earn_src = "yfinance" + (" · FinMind/TWSE(월매출)" if (is_tw and tw_revenue_html) else "")
    earnings_pane = f"""<div class="si-pane" id="si-earnings">
  <div class="si-section">
    <div class="si-section-title">최근 실적</div>
    {earnings_table}
  </div>
  {tw_revenue_html}
  {_src_foot}출처: {earn_src}</div>
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
  <tbody>{r_rows}</tbody></table>"""
    elif (is_jp or is_tw) and (mkt.get("consensus") or {}).get("target_mean"):
        # C1(2026-06-16): JP(Kabutan)/TW(cnyes)는 per-broker 액션 피드가 없지만
        # 종합 레이팅+목표가+기관수+최신일은 있어 최소 1행으로 표시 → 리서치
        # 탭 빈칸 해소. (per-firm 피드 소스 부재는 CLAUDE.md TODO 로 보류 중.)
        _c = mkt["consensus"]
        _crating = esc(str(_c.get("rating") or "—"))
        _ctgt = _c.get("target_mean")
        _ctgt_str = f"{csym}{_fmt_num(_ctgt, decimals=2 if currency not in _0dec else 0)}" if _ctgt else "—"
        _cnn = _c.get("n_analysts")
        _cnn_str = f"{_cnn}명" if _cnn else "—"
        _cdate = esc(str(_c.get("last_report_date") or "—"))
        research_table = f"""<table class="si-table">
  <thead><tr><th>최신 리포트</th><th>소스</th><th>종합 의견</th><th class="num">목표가</th><th class="num">기관수</th></tr></thead>
  <tbody><tr><td>{_cdate}</td><td>{esc(str(_c.get("source","")))}</td><td>{_crating}</td><td class="num">{_ctgt_str}</td><td class="num">{_cnn_str}</td></tr></tbody></table>"""
    else:
        research_table = '<div class="si-empty">리서치 액션 데이터가 없습니다.</div>'

    # 리서치 탭 집계 목표가 요약(사용자 2026-06-28) — US upgrades_downgrades 피드엔
    # per-firm 목표가가 없어(yfinance 컬럼 = Firm/To/From/Action 뿐) 의견변경만 보였다.
    # 집계 목표주가(평균·범위·기관수·상승여력)를 표 위 한 줄로 보강. target_mean 있으면
    # 전 시장 공통(universal — KR/JP/TW 도 동일 노출, 시장 게이트 없음). target_mean
    # 셀은 data-q 로 라이브 오버레이가 함께 갱신, 상승여력은 스냅샷 기준 정적.
    research_target_html = ""
    _rt_mean = si.get("target_mean")
    if isinstance(_rt_mean, (int, float)) and _rt_mean:
        _rt_dec = 2 if currency not in _0dec else 0
        _rt_lo, _rt_hi, _rt_n = si.get("target_low"), si.get("target_high"), si.get("num_analysts")
        _parts = [f'목표주가 <span data-q="target_mean" style="font-weight:700">'
                  f'{csym}{_fmt_num(_rt_mean, decimals=_rt_dec)}</span>']
        if isinstance(_rt_lo, (int, float)) and isinstance(_rt_hi, (int, float)):
            _parts.append(f'범위 {csym}{_fmt_num(_rt_lo, decimals=_rt_dec)}'
                          f'~{csym}{_fmt_num(_rt_hi, decimals=_rt_dec)}')
        if _rt_n:
            _parts.append(f'{_rt_n}명')
        if cur_price and cur_price > 0:
            _up = (_rt_mean - cur_price) / cur_price * 100
            _parts.append(f'상승여력 <span style="color:{"#26a69a" if _up >= 0 else "#e2574c"}">'
                          f'{"+" if _up >= 0 else ""}{_up:.1f}%</span>')
        research_target_html = (
            '<div style="font-size:13px;margin-bottom:10px;padding:8px 12px;'
            f'background:var(--bg);border-radius:6px">{" · ".join(_parts)}</div>')

    _res_src = ("한경 컨센서스" if kr_reports
                else "Kabutan" if (is_jp and (mkt.get("consensus") or {}).get("target_mean"))
                else "鉅亨網" if (is_tw and (mkt.get("consensus") or {}).get("target_mean"))
                else "yfinance")
    research_pane = f"""<div class="si-pane" id="si-research">
  <div class="si-section">
    <div class="si-section-title">리서치 액션</div>
    {research_target_html}
    {research_table}
  </div>
  {_src_foot}출처: {_res_src}</div>
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
    <div class="si-section-title">임원 · 주요주주 지분</div>
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
                le_html = ""
                if le is not None:
                    le_warn = " ⚠️ ceiling 임박" if le >= 95 else (" ⚠️ 제한 접근" if le >= 80 else "")
                    le_html = f'<tr><td>한도소진율</td><td class="num">{le:.2f}%{le_warn}</td></tr><tr><td>한도주식수</td><td class="num">{lim_s:,}주</td></tr>'
                kr_foreign_html = f"""<div class="si-section">
    <div class="si-section-title">외국인 보유현황 ({fd})</div>
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
    # 비-KR: 네이버 한국어 해외뉴스 우선(worldstock, ₩0·번역 불요, 사용자 2026-06-16)
    # → 실패/KR 은 stored(yfinance 등)+translate 폴백. 디스크 캐시(12h)라 bulk regen
    # 부하 무. ⚠️ 네이버는 현재 시점 기사(분석시점 아님) — 개요와 달리 뉴스는 최신.
    _nv_news = None
    if not is_kr:
        try:
            from bot.naver_overview import fetch_naver_world_news
            _nv_news = fetch_naver_world_news(ticker, max_items=10)
        except Exception:
            _nv_news = None
    if _nv_news:
        n_items = ""
        for n in _nv_news:
            title = esc(n.get("title", ""))
            publisher = esc(n.get("publisher", ""))
            ndate = esc(n.get("date", ""))
            link = n.get("link", "")
            sub = esc(n.get("subcontent", ""))
            # worldStock 뉴스는 기사별 URL 이 없다(oid=fnGuide 고정, FnGuide 와이어
            # 요약) → 제목은 종목 네이버 페이지로 링크하고, **본문 요약(subcontent)을
            # 카드에 인라인** 노출해 클릭 없이 바로 읽히게(사용자 2026-06-19 '뉴스
            # 누르면 다 같은데로' — 깨진 deep-link 폐기, 내용 직접 표시).
            title_html = (f'<a href="{esc(link)}" target="_blank" rel="noopener">{title}</a>'
                          if link else title)
            sub_html = f'<div class="si-news-sub">{sub}</div>' if sub else ""
            n_items += (f'<div class="si-news-item"><div class="si-news-title">{title_html}</div>'
                        f'{sub_html}'
                        f'<div class="si-news-meta">{publisher} · {ndate}</div></div>\n')
        news_html = n_items
    elif news:
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

    # ── AV 뉴스 감성 (B2, 2026-06-16) — 기사별 사전 점수(-1~+1) 집계.
    # cache_only=True: 분석이 이미 캐시한 종목만 표시, 대시보드 조회가 AV
    # 무료한도(25/day)를 절대 소진하지 않음(미분석 종목은 배지 생략). 전 시장.
    av_sent_html = ""
    try:
        from bot.av_sentiment_client import fetch_news_sentiment
        _avs = fetch_news_sentiment(ticker, cache_only=True)
        _ag = (_avs or {}).get("aggregate") or {}
        if _ag.get("total"):
            bull, bear, neu = _ag.get("bullish", 0), _ag.get("bearish", 0), _ag.get("neutral", 0)
            avg = _ag.get("avg_score", 0.0) or 0.0
            tot = _ag.get("total", 0)
            _lab = "강세" if avg >= 0.15 else "약세" if avg <= -0.15 else "중립"
            _lc = "#26a69a" if avg >= 0.15 else "#e2574c" if avg <= -0.15 else "var(--fg-soft)"
            av_sent_html = f"""<div class="si-section">
    <div class="si-section-title">뉴스 감성 (Alpha Vantage)</div>
    <div style="font-size:14px">종합 <span style="color:{_lc};font-weight:600">{_lab}</span> (평균 {avg:+.2f}) · 🐂 {bull} · 😐 {neu} · 🐻 {bear} · 총 {tot}건</div>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">기사별 사전 점수 집계 · 분석 시점 캐시</div>
  </div>"""
    except Exception as exc:
        log.debug("detail: AV sentiment %s: %s", ticker, exc)

    # 시장별 1차 뉴스 소스(개별 기사 publisher 는 행마다 별도 표시).
    news_src = ("네이버 해외뉴스" if _nv_news else
                "Naver Finance" if is_kr else "Kabutan" if is_jp
                else "cnyes" if is_tw else "동방재부(AKShare)" if is_cn
                else "yfinance")
    news_pane = f"""<div class="si-pane" id="si-news">
  {av_sent_html}
  <div class="si-section">
    <div class="si-section-title">주요 뉴스</div>
    {news_html}
  </div>
  {_src_foot}출처: {news_src}{" · Alpha Vantage(감성)" if av_sent_html else ""}</div>
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
      <div class="si-section-title">투자자별 순매수</div>
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

        # Multi-period trends — batch regen reads cache only (no network),
        # live render does full fetch. Cached data from last analysis keeps
        # the table visible even during startup/midnight regen.
        trend_html = ""
        try:
            from bot.pykrx_client import get_kr_multi_period_trends
            mp = get_kr_multi_period_trends(ticker, cache_only=_BATCH_REGEN)
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
      <div class="si-section-title">다기간 추이 · 외국인보유율·공매도 잔고율</div>
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
  {_src_foot}출처: KIS · pykrx</div>
</div>"""

    # CN/HK 스톡커넥트(Stock Connect) flow
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
    <div class="si-section-title">스톡커넥트 시장 전체 자금 흐름 (5일 누적)</div>
    <table class="si-table"><thead><tr><th>구분</th><th class="num">5일 순매수</th><th>방향</th></tr></thead><tbody>
      <tr><td>북향 (외국인 → 중국 A주)</td>{_cn_flow(nb)}<td>{esc(nd)}</td></tr>
      <tr><td>남향 (중국 본토 → 홍콩)</td>{_cn_flow(sb)}<td>{esc(sd)}</td></tr>
    </tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">※ 이 종목의 개별 수급이 아닌 스톡커넥트(Stock Connect) 시장 전체 집계. 출처: 동방재부(Eastmoney · 12시간 캐시)</div>
  </div>
  {_src_foot}출처: AKShare · 동방재부(Eastmoney)</div>
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
    <div class="si-section-title">JPX 시장 전체 주간 수급 (백만엔)</div>
    <table class="si-table"><thead><tr><th>주체</th>{wk_hdrs}</tr></thead><tbody>{jpx_body}</tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">※ 이 종목의 개별 수급이 아닌 일본 시장 전체 집계 · 최신 기준: {esc(str(jpx_rows[0].get("date",""))[:10])}. 출처: JPX 투자부문별 매매현황 (주 1회 목/금 발표, 96시간 캐시)</div>
  </div>
  {_src_foot}출처: JPX</div>
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
                tw_labels = [("외국인", "foreign"), ("투신", "trust"),
                             ("자기매매", "dealer"), ("합계", "total")]
                tw_body = ""
                for label, key in tw_labels:
                    tw_body += f"<tr><td>{label}</td>{_tw_cell(tw_today.get(key))}{_tw_cell(tw_5d.get(key))}</tr>\n"
                flow_pane = f"""<div class="si-pane" id="si-flow">
  <div class="si-section">
    <div class="si-section-title">외국인·기관 매매동향 (종목별)</div>
    <table class="si-table"><thead><tr><th>주체</th><th class="num">당일</th><th class="num">5일 누적</th></tr></thead><tbody>{tw_body}</tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">출처: {'TWSE' if ticker.upper().endswith('.TW') else 'TPEx'} 일별 매매주체 순매수</div>
  </div>
  {_src_foot}출처: FinMind · {'TWSE' if ticker.upper().endswith('.TW') else 'TPEx'}</div>
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
      <div class="si-section-title">내부자 거래 (60일)</div>
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
      <div class="si-section-title">내부자 심리 (MSPR)</div>
      <table class="si-table"><thead><tr><th>월</th><th class="num">MSPR</th><th class="num">건수 변화</th></tr></thead><tbody>{is_rows}</tbody></table>
      <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">MSPR: Monthly Share Purchase Ratio — 양수=순매수, 음수=순매도</div>
    </div>""")
        except Exception as exc:
            log.info("detail: finnhub insider %s: %s", ticker, exc)

        # Finnhub 애널리스트 의견 분포 추이 (B1, 2026-06-16) — 월별
        # StrongBuy/Buy/Hold/Sell/StrongSell 카운트 + 매수비중. 컨센서스
        # 평균 한 점이 아니라 '시간에 따른 의견 쏠림 변화'를 보여줌.
        try:
            from bot.finnhub_client import fetch_recommendation_trends, finnhub_key_ready
            if finnhub_key_ready():
                rt = fetch_recommendation_trends(ticker)
                if rt:
                    rt_rows = ""
                    for r in rt[:6]:
                        period = esc(str(r.get("period", ""))[:7])
                        sb = int(r.get("strongBuy", 0) or 0)
                        b = int(r.get("buy", 0) or 0)
                        h = int(r.get("hold", 0) or 0)
                        s = int(r.get("sell", 0) or 0)
                        ss = int(r.get("strongSell", 0) or 0)
                        tot = sb + b + h + s + ss
                        if not tot:
                            continue
                        buy_pct = (sb + b) / tot * 100
                        bc = "#26a69a" if buy_pct >= 60 else "#e2574c" if buy_pct < 40 else ""
                        bs = f' style="color:{bc}"' if bc else ""
                        rt_rows += (f'<tr><td>{period}</td><td class="num">{sb}</td><td class="num">{b}</td>'
                                    f'<td class="num">{h}</td><td class="num">{s}</td><td class="num">{ss}</td>'
                                    f'<td class="num"{bs}>{buy_pct:.0f}%</td></tr>\n')
                    if rt_rows:
                        us_flow_parts.append(f"""<div class="si-section">
      <div class="si-section-title">애널리스트 의견 분포 추이</div>
      <table class="si-table"><thead><tr><th>월</th><th class="num">강력매수</th><th class="num">매수</th><th class="num">보유</th><th class="num">매도</th><th class="num">강력매도</th><th class="num">매수%</th></tr></thead><tbody>{rt_rows}</tbody></table>
    </div>""")
        except Exception as exc:
            log.info("detail: finnhub rec trends %s: %s", ticker, exc)

        if us_flow_parts:
            flow_pane = '<div class="si-pane" id="si-flow">\n  ' + "\n  ".join(us_flow_parts) + f'\n  {_src_foot}출처: yfinance · SEC · Finnhub</div>\n</div>'

    # Placeholder so the JS overlay can find the element by ID during
    # batch regen (when all live-fetch blocks are skipped → flow_pane="").
    if not flow_pane:
        flow_pane = '<div class="si-pane" id="si-flow"></div>'

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

    _risk_src = "KRX · FSC" if is_kr else "AKShare" if is_cn else "yfinance"
    risk_pane = ""
    if risk_parts:
        risk_pane = '<div class="si-pane" id="si-risk">\n  ' + "\n  ".join(risk_parts) + f'\n  {_src_foot}출처: {_risk_src}</div>\n</div>'
    # Placeholder so the JS overlay can swap risk content via live quote.
    if not risk_pane:
        risk_pane = '<div class="si-pane" id="si-risk"></div>'

    # ── 배당 이력 (밸류에이션 pane 하단에 추가) ────────────────────
    divs = si.get("dividends", [])
    div_html = ""
    if divs:
        # 최신이 위로 (사용자 2026-06-20) — 배당락일 내림차순. 날짜 문자열 정렬이라
        # 'YYYY-MM-DD' 사전식 = 시간순 일치. 날짜 없는 행은 맨 아래.
        divs = sorted(divs, key=lambda d: str(d.get("date") or ""), reverse=True)
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
                # 단위 축약 (사용자 2026-06-17 '단위 너무 길다') — 정수/실수 동일
                # 처리(옛 정수 분기가 $113,097,000,000 전체 자릿수 노출했음).
                if v is None:
                    return "—"
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    return str(v)
                if key == "eps_diluted":
                    return f"{v:,.2f}"                       # EPS = 평문(통화기호 X)
                if u == "shares" or key == "shares":         # 발행주식수 = 주식 수($ X)
                    if abs(v) >= 1e8:
                        return f"{v/1e8:,.2f}억주"
                    if abs(v) >= 1e4:
                        return f"{v/1e4:,.0f}만주"
                    return f"{v:,.0f}주"
                sym = "$" if u in ("USD", None) else ""      # ADR 외화(EUR/JPY)는 기호 생략
                if abs(v) >= 1e12:
                    return f"{sym}{v/1e12:,.2f}T"
                if abs(v) >= 1e9:
                    return f"{sym}{v/1e9:,.2f}B"
                if abs(v) >= 1e6:
                    return f"{sym}{v/1e6:,.1f}M"
                return f"{sym}{v:,.0f}"
            ann_str = _xbrl_fmt(ann.get("val"), unit)
            lat_str = _xbrl_fmt(lat.get("val"), unit)
            fy_str = f"FY{ann.get('fy', '')}" if ann.get("fy") else "—"
            lat_form = lat.get("form", "")
            xb_rows += f"<tr><td>{esc(label)}</td><td class='num'>{ann_str}</td><td>{fy_str}</td><td class='num'>{lat_str}</td><td>{esc(lat_form)}</td></tr>\n"
        us_xbrl_html = f"""<div class="si-section">
    <div class="si-section-title">SEC XBRL 재무</div>
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
    <div class="si-section-title">내부자 거래</div>
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
            # B5: KR 구조화 요약(dart_detail) 한 줄 — 제목 아래 회색 보조줄.
            _dsumm = disc.get("summary")
            summ_html = (f'<div style="font-size:12px;color:var(--fg-soft);margin-top:2px">{esc(str(_dsumm))}</div>'
                         if _dsumm else "")
            d_rows += f"<tr><td>{d_date}</td><td>{title_html}{summ_html}</td><td>{d_reporter}</td></tr>\n"
        disclosures_pane = f"""<div class="si-pane" id="si-disclosures">
  <div class="si-section">
    <div class="si-section-title">공시</div>
    <table class="si-table">
      <thead><tr><th>날짜</th><th>제목</th><th>출처</th></tr></thead>
      <tbody>{d_rows}</tbody>
    </table>
  </div>
  {_src_foot}출처: {esc(disc_source)}{' · JP 6개월' if is_jp else ' · 1년'}</div>
</div>"""

    # Placeholder so the JS overlay can find the element by ID during
    # batch regen (when all live-fetch blocks are skipped → disclosures_pane="").
    if not disclosures_pane:
        disclosures_pane = '<div class="si-pane" id="si-disclosures"></div>'

    # ── 이벤트 타임라인 pane ────────────────────────────────────
    _tl_sources = " · ".join(dict.fromkeys(
        [s for s in ["yfinance", disc_source, news_src] if s]))
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
  {_src_foot}출처: {_tl_sources}</div>
</div>"""
    else:
        timeline_pane = f"""<div class="si-pane" id="si-timeline">
  <div class="si-empty">이벤트 데이터가 없습니다.</div>
  {_src_foot}출처: {_tl_sources}</div>
</div>"""

    # ── 기술 분석 pane (검증 지표 세트 무료 + 강세/약세 토론 LLM 1콜, cost-gated) ──
    # 지표는 탭 활성화 시 /api/technical 로 lazy fetch(즉시·무과금). 토론은 '실행'
    # 버튼 클릭 시에만 &run=1 단일 Gemini flash-lite 호출. (티커+KST날짜) 캐시라
    # 같은 날 재클릭 무과금. 우리 종목분석 방법론(강세/약세·축별판정·시나리오) 재사용.
    technical_pane = f"""<div class="si-pane" id="si-technical">
  <div class="si-section">
    <div class="si-section-title">검증 지표 세트
      <span style="font-size:11px;color:var(--fg-soft);font-weight:400">· 일봉 1년 · 무료</span></div>
    <div id="si-tech-ind-status" style="font-size:12px;color:var(--fg-soft)">지표 로딩…</div>
    <div id="si-tech-ind"></div>
  </div>
  <div class="si-section">
    <div class="si-section-title">강세 vs 약세 토론
      <span style="font-size:11px;color:var(--fg-soft);font-weight:400">· 5거래일 방향성 · AI</span></div>
    <div style="font-size:12px;color:var(--fg-soft);margin-bottom:8px;line-height:1.5">
      위 지표만 근거로 강세·약세 연구원의 논거를 정리하고 축별 판정 후 종합 판정·조건부
      시나리오를 제시합니다. AI(Gemini) 호출이라 소액 비용(약 ₩2~7)이 발생하며,
      <b>같은 날 같은 종목 재실행은 무과금</b>(캐시)입니다.
    </div>
    <button type="button" id="si-tech-run"
      style="background:var(--accent);color:#fff;border:none;border-radius:6px;
             padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer">
      🧮 기술 분석 실행 (Gemini)</button>
    <div id="si-tech-debate-status" style="font-size:12px;color:var(--fg-soft);margin-top:8px"></div>
    <div id="si-tech-debate" style="margin-top:12px"></div>
  </div>
  <div style="font-size:11px;color:var(--fg-soft);margin-top:8px;line-height:1.5">
    검증 지표=yfinance 일봉 산출(EMA·SMA·RSI·MACD·볼린저·ATR·VWMA·거래량) ·
    토론=Gemini AI 단일 호출(지표만 근거, 펀더멘털·뉴스 비포함) · 5거래일 단기 방향 참고용(투자권유 아님)</div>
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
    # TW 분기 재무 박스 (B4 2026-06-16) — FinMind 손익/재무상태/현금흐름. US SEC
    # XBRL·KR DART 박스의 TW 등가물(밸류에이션 탭). type 값 VM probe 확정(영문).
    tw_fin_html = ""
    tw_fin = si.get("tw", {}).get("financials") if is_tw else None
    if tw_fin and isinstance(tw_fin, list) and tw_fin:
        def _twf(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return "—"
            a = abs(v)
            if a >= 1e12:
                return f"NT${v / 1e12:,.2f}兆"
            if a >= 1e8:
                return f"NT${v / 1e8:,.0f}億"
            return f"{v:,.0f}"
        _cols = tw_fin[:4]
        _hdr = "<th>항목</th>" + "".join(
            f"<th class='num'>{esc(str(c.get('date', '')))}</th>" for c in _cols)
        _rowdef = [("매출", "revenue"), ("매출총이익", "gross_profit"),
                   ("영업이익", "operating_income"), ("순이익", "net_income"),
                   ("EPS", "eps"), ("자산총계", "total_assets"),
                   ("부채총계", "total_liab"), ("자본총계", "equity"),
                   ("영업현금흐름", "op_cf")]
        _body = ""
        for _label, _key in _rowdef:
            _cells = ""
            for c in _cols:
                v = c.get(_key)
                if _key == "eps":
                    _cells += f"<td class='num'>{('%.2f' % float(v)) if v is not None else '—'}</td>"
                else:
                    _cells += f"<td class='num'>{_twf(v) if v is not None else '—'}</td>"
            _body += f"<tr><td>{_label}</td>{_cells}</tr>"
        tw_fin_html = f"""<div class="si-section">
    <div class="si-section-title">분기 재무 (FinMind)</div>
    <table class="si-table"><thead><tr>{_hdr}</tr></thead><tbody>{_body}</tbody></table>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">출처: FinMind(TWSE 재무보고) · 분기말 기준</div>
  </div>"""

    _val_src = "yfinance" + (" · SEC XBRL" if is_us else " · DART" if is_kr
                             else " · AKShare" if is_cn else " · FinMind" if is_tw else "")
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
  {tw_fin_html}
  {div_html}
  {_src_foot}출처: {_val_src}</div>
</div>"""

    # ── 밴드차트 pane (PER/PBR — FnGuide BandChart 충실 재현, lazy /api/band) ──
    band_pane = ""
    if band_tab:
        band_pane = f"""<div class="si-pane" id="si-bandchart">
  <div class="si-section">
    <div class="si-section-title">밴드차트 (PER/PBR 멀티플)</div>
    <div style="font-size:12px;color:var(--fg-soft);margin-bottom:8px;line-height:1.5">
      파란선=주가 · 밴드선=PER/PBR 멀티플(최고·중상·중하·최저)별 적정주가 · 세로 점선 오른쪽=현재 이후(컨센서스 전망 구간).
      FnGuide 밴드차트 데이터(밴드·멀티플·전망 동일).
    </div>
    <div id="si-band-status" style="font-size:12px;color:var(--fg-soft)">차트 로딩…</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px" id="si-band-grid">
      <div><div class="si-section-title" style="font-size:13px">PER 밴드</div>
        <div id="si-band-per"></div>
        <div id="si-band-per-lg" style="font-size:11px;margin-top:4px;display:flex;flex-wrap:wrap;gap:8px"></div></div>
      <div><div class="si-section-title" style="font-size:13px">PBR 밴드</div>
        <div id="si-band-pbr"></div>
        <div id="si-band-pbr-lg" style="font-size:11px;margin-top:4px;display:flex;flex-wrap:wrap;gap:8px"></div></div>
    </div>
    {_src_foot}출처: FnGuide(네이버 임베드) · BandChart</div>
  </div>
  <script>{_BAND_JS}</script>
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
            # 야후 annual 정렬이 종목마다 달라(최신우선/과거우선 혼재) → 순서 가정
            # 제거: period(연도)로 명시 정렬 후 최근 4개. 차트=오름차순(좌→우 시간순),
            # YoY 표=내림차순(최신 위, 아래 sort). blind reversed 가 일부 종목서 거꾸로
            # 나오던 것 차단(사용자 2026-06-17 AMAT YoY 최신 아래로). 정렬 전 [:4] 가
            # 과거 4개를 자르던 위험도 해소(이제 정렬 후 최근 4개).
            _annual_sorted = sorted(is_annual, key=lambda r: str(r.get("period", "")))
            chart_items = []
            for r in _annual_sorted[-4:]:
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

                # YoY growth rates — 최신 연도가 위로 (사용자 2026-06-17 '최신이
                # 위쪽'). 차트(수익성 추이)는 시간순 좌→우 유지, 표만 내림차순.
                _grows = []
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
                    _grows.append((period, f"<tr><td>{period}</td>{cells}</tr>"))
                # 최신 연도 위로 — period(연도) 기준 내림차순(blind reverse 아님,
                # 입력 순서 무관 → 종목마다 야후 정렬 달라도 항상 최신 top).
                _grows.sort(key=lambda x: x[0], reverse=True)
                growth_rows = "\n".join(row for _, row in _grows)
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
  {_src_foot}출처: yfinance</div>
</div>"""

    # Placeholder so the JS overlay can find the element by ID when
    # financials data is absent (common during batch regen).
    if not financials_pane:
        financials_pane = '<div class="si-pane" id="si-financials"></div>'

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
  {_src_foot}출처: yfinance</div>
</div>"""

    # Placeholder so the JS overlay can find the element by ID when
    # peer_comps data is absent (common during batch regen).
    if not peers_pane:
        peers_pane = '<div class="si-pane" id="si-peers"></div>'

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
    <div class="si-section-title">5%+ 대량보유</div>
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
    <div class="si-section-title">내부자 지분</div>
    <table class="si-table">
      <thead><tr><th>성명</th><th>직위</th><th class="num">보유주식</th><th class="num">지분율</th></tr></thead>
      <tbody>{ti_rows}</tbody>
    </table>
  </div>"""

    # TW 외국인 보유 현황 (2026-06-16 정정) — VM probe 로 FinMind
    # TaiwanStockShareholding 이 TDCC 보유구간 분산이 아니라 외국인 투자 현황+
    # 발행주식수임을 확인(옛 'TDCC 분산' 표는 그 필드가 없어 항상 빈 표였음).
    # 외국인 보유비율은 TW 핵심 수급 신호.
    tw_disp_html = ""
    tw_fgn = si.get("tw", {}).get("foreign", {}) if is_tw else {}
    if tw_fgn and tw_fgn.get("foreign_ratio") is not None:
        def _pctf(v):
            try:
                return f"{float(v):.2f}%"
            except (TypeError, ValueError):
                return "—"
        _fr = _pctf(tw_fgn.get("foreign_ratio"))
        _rr = tw_fgn.get("remain_ratio")
        _rr_html = (f' · 한도 여유 {_pctf(_rr)}'
                    if _rr is not None else "")
        _si = tw_fgn.get("shares_issued")
        _si_html = (f' · 발행주식수 {int(_si):,}'
                    if isinstance(_si, (int, float)) and _si else "")
        _fdate = esc(str(tw_fgn.get("date") or ""))
        tw_disp_html = f"""<div class="si-section">
    <div class="si-section-title">외국인 보유 현황</div>
    <div style="font-size:14px">외국인 보유비율 <b>{_fr}</b>{_rr_html}{_si_html}</div>
    <div style="font-size:11px;color:var(--fg-soft);margin-top:4px">출처: FinMind(TaiwanStockShareholding){(' · ' + _fdate) if _fdate else ''}</div>
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
    <div class="si-section-title">주요 유통주주</div>
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
    <div class="si-section-title">내부자 거래 요약</div>
    <div style="display:flex;gap:20px;align-items:center;margin:8px 0">
      <div style="text-align:center"><span style="font-size:24px;font-weight:700;color:#26a69a">{buys}</span><div style="font-size:12px;color:#999">매수</div></div>
      <div style="flex:1;height:12px;background:#333;border-radius:6px;overflow:hidden;display:flex">
        <div style="width:{buy_pct:.0f}%;background:#26a69a"></div>
        <div style="width:{sell_pct:.0f}%;background:#e2574c"></div>
      </div>
      <div style="text-align:center"><span style="font-size:24px;font-weight:700;color:#e2574c">{sells}</span><div style="font-size:12px;color:#999">매도</div></div>
    </div>
  </div>"""

    _hold_src = ("yfinance · DART · pykrx" if is_kr else "yfinance · EDINET" if is_jp
                 else ("yfinance · MOPS · FinMind" if tw_disp_html else "yfinance · MOPS") if is_tw
                 else "yfinance · AKShare" if is_cn
                 else "yfinance · SEC")
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
  {tw_disp_html}
  {cn_holders_html}
  {_src_foot}출처: {_hold_src}</div>
</div>"""

    # ── 투자정보 요약 카드 (D, 사용자 2026-06-28) — 여러 탭에 흩어진 핵심
    # 멀티플(밸류에이션)·EPS/BPS·컨센서스(컨센서스 탭)를 종합 탭 첫 화면에 1카드로
    # 집약. 데이터는 이미 fetch 된 si 값 재사용 → 추가 비용·네트워크 0. data-q 부여
    # 셀은 라이브 오버레이가 querySelectorAll 로 함께 갱신(밸류에이션 탭과 동일 포맷).
    def _ic_mult(key):
        v = si.get(key)
        return f"{float(v):.2f}x" if isinstance(v, (int, float)) else "—"
    def _ic_ps(key):
        v = si.get(key)
        return f"{float(v):,.2f}" if isinstance(v, (int, float)) else "—"
    def _ic_price(v):
        return ("—" if not isinstance(v, (int, float))
                else f"{csym}{_fmt_num(v, decimals=2 if currency not in _0dec else 0)}")
    _ic_dy = _safe_dy_pct(si.get("dividendYield"), si.get("dividendRate"),
                          si.get("current_price"))
    _ic_dy_s = f"{_ic_dy:.2f}%" if isinstance(_ic_dy, (int, float)) else "—"
    _ic_upside_s = "—"
    if isinstance(target_mean, (int, float)) and cur_price and cur_price > 0:
        _u = (target_mean - cur_price) / cur_price * 100
        _ic_upside_s = (f'<span style="color:{"#26a69a" if _u >= 0 else "#e2574c"}">'
                        f'{"+" if _u >= 0 else ""}{_u:.1f}%</span>')
    _ic_range_s = (f"{_ic_price(target_low)} ~ {_ic_price(target_high)}"
                   if isinstance(target_low, (int, float))
                   and isinstance(target_high, (int, float)) else "—")
    _ic_analysts_s = f"{n_analysts}명" if n_analysts else "—"
    _ic_left = (
        f'<tr><td>PER (후행)</td><td class="num" data-q="trailingPE">{_ic_mult("trailingPE")}</td></tr>'
        f'<tr><td>PER (선행)</td><td class="num" data-q="forwardPE">{_ic_mult("forwardPE")}</td></tr>'
        f'<tr><td>PBR</td><td class="num" data-q="priceToBook">{_ic_mult("priceToBook")}</td></tr>'
        f'<tr><td>PSR</td><td class="num" data-q="priceToSalesTrailing12Months">{_ic_mult("priceToSalesTrailing12Months")}</td></tr>'
        f'<tr><td>EPS (후행)</td><td class="num" data-q="trailingEps">{_ic_ps("trailingEps")}</td></tr>'
        f'<tr><td>BPS</td><td class="num" data-q="bookValue">{_ic_ps("bookValue")}</td></tr>'
        f'<tr><td>배당수익률</td><td class="num" data-q="dividendYield">{_ic_dy_s}</td></tr>'
    )
    _ic_right = (
        f'<tr><td>투자의견</td><td class="num">{esc(rec_label)}</td></tr>'
        f'<tr><td>목표주가</td><td class="num" data-q="target_mean">{_ic_price(target_mean)}</td></tr>'
        f'<tr><td>상승여력</td><td class="num">{_ic_upside_s}</td></tr>'
        f'<tr><td>목표가 범위</td><td class="num">{_ic_range_s}</td></tr>'
        f'<tr><td>애널리스트</td><td class="num">{_ic_analysts_s}</td></tr>'
    )
    overview_card = f"""<div class="si-section" id="si-invest-card">
  <div class="si-section-title">투자정보 요약</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px;align-items:start">
    <table class="si-table"><thead><tr><th>멀티플·주당</th><th class="num">값</th></tr></thead><tbody>{_ic_left}</tbody></table>
    <table class="si-table"><thead><tr><th>컨센서스</th><th class="num">값</th></tr></thead><tbody>{_ic_right}</tbody></table>
  </div>
  <div style="font-size:11px;color:var(--fg-soft);margin-top:6px">멀티플=밸류에이션 탭 · 컨센서스=컨센서스 탭 상세 · 상승여력·목표가범위=현재가 대비</div>
</div>"""

    # Return a dict with separate pieces so _render_detail can wrap
    # the chart/summary/report inside the overview pane. The per-pane
    # map lets build_live_quote(full=True) hand back just the heavy,
    # filing-/daily-cadence panes for the client to swap in.
    return {
        "header": header,
        "tabs": tabs_html,
        "overview_card": overview_card,
        "tab_js": tab_js,
        "other_panes": company_pane + "\n" + consensus_pane + "\n" +
                       valuation_pane + "\n" + band_pane + "\n" + financials_pane + "\n" +
                       peers_pane + "\n" + earnings_pane + "\n" +
                       research_pane + "\n" + holders_pane + "\n" +
                       flow_pane + "\n" + disclosures_pane + "\n" +
                       risk_pane + "\n" + news_pane + "\n" + timeline_pane + "\n" +
                       technical_pane + f"\n<script>{_TECHNICAL_JS}</script>",
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
  function applyFull(j){
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
  }
  // 로딩 상태등 (사용자 2026-06-10): 빨강=로딩 중, 초록=전체(가격+패널) 신선
  // 로딩 완료. 가격(LIGHT)과 패널(FULL, stale 면 백그라운드 force 까지) 둘 다
  // 끝나야 초록 → 사용자가 새로고침 안 눌러도 완료 시점을 안다.
  var lightDone = false, fullDone = false;
  function setDot(){
    var d = document.getElementById('q-dot'); if (!d) return;
    if (lightDone && fullDone){ d.className = 'q-dot q-dot-done'; d.title = '전체 로딩 완료'; }
    else { d.className = 'q-dot q-dot-load'; d.title = '데이터 로딩 중…'; }
  }
  // 라이브 배지를 차트 헤드라인 오른쪽 슬롯으로 이동 — 별도 줄 제거(사용자
  // 2026-06-11). 차트 없는 페이지는 원래 자리 유지. 슬롯/배지가 늦게 뜨는
  // lookup 비동기 주입 대비 재시도.
  (function relocateLive(n){
    var slot = document.getElementById('chart-live-slot');
    var stat = document.querySelector('.si-quote-status');
    if (slot && stat){
      while (stat.firstChild) slot.appendChild(stat.firstChild);
      stat.style.display = 'none';
    } else if (n < 12) setTimeout(function(){ relocateLive(n+1); }, 400);
  })(0);
  // LIGHT — fast, always fresh
  fetch(base + 'api/quote?ticker=' + encodeURIComponent(NOAH_TICKER), {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j || !j.ok || !j.quote){ badge(null, false); lightDone = true; setDot(); return; }
      lastFmt = j.quote.fmt;
      applyFmt(j.quote.fmt); applyPos(j.quote.pos); badge(j.quote.meta, true);
      lightDone = true; setDot();
    })
    .catch(function(){ badge(null, false); lightDone = true; setDot(); });
  // FULL — may return stale cache (instant) then background-refresh
  var fullUrl = base + 'api/quote?ticker=' + encodeURIComponent(NOAH_TICKER) + '&full=1';
  fetch(fullUrl, {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(j){
      applyFull(j);
      if (j && j.stale){
        // stale → 백그라운드 force 까지 끝나야 fullDone(초록).
        setTimeout(function(){
          fetch(fullUrl + '&force=1', {cache:'no-store'})
            .then(function(r){ return r.json(); })
            .then(function(j2){ applyFull(j2); fullDone = true; setDot(); })
            .catch(function(){ fullDone = true; setDot(); });
        }, 2000);
      } else { fullDone = true; setDot(); }
    })
    .catch(function(){ fullDone = true; setDot(); });
  // Refresh button
  var rb = document.getElementById('q-refresh');
  if (rb) rb.addEventListener('click', function(){
    rb.textContent = '⏳'; rb.disabled = true;
    fullDone = false; lightDone = false; setDot();
    fetch(fullUrl + '&force=1', {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(j){ applyFull(j); rb.textContent = '🔄'; rb.disabled = false; fullDone = true; setDot(); })
      .catch(function(){ rb.textContent = '🔄'; rb.disabled = false; fullDone = true; setDot(); });
    fetch(base + 'api/quote?ticker=' + encodeURIComponent(NOAH_TICKER), {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (!j || !j.ok || !j.quote){ lightDone = true; setDot(); return; }
        lastFmt = j.quote.fmt;
        applyFmt(j.quote.fmt); applyPos(j.quote.pos); badge(j.quote.meta, true);
        lightDone = true; setDot();
      }).catch(function(){ lightDone = true; setDot(); });
  });
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
    si_overview_card = si_parts.get("overview_card", "") if si_parts else ""
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
  <a class="back" href="../index.html">← 아카이브로 돌아가기</a>
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
  {si_overview_card}
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
        _idx_html, _idx_frags = _render_index(records)
        # 프래그먼트를 index 보다 **먼저** + 원자 쓰기(tmp+replace) — 새 index 가
        # 아직 없는/반쯤 쓰인 프래그먼트를 참조해 404·잘림 로드되는 창 차단
        # (리뷰 finding #5, 월 롤오버 직후 첫 regen 이 대표 케이스).
        for _fn, _fc in _idx_frags.items():
            try:
                _ft = (ARCHIVE_ROOT / _fn).with_suffix(".tmp")
                _ft.write_text(_fc, encoding="utf-8")
                _ft.replace(ARCHIVE_ROOT / _fn)
            except Exception as _fe:
                log.warning("dashboard: index fragment %s write failed: %s",
                            _fn, _fe)
        # 업데이트 배너(1분 팝업 + 30분 기준선) — index 도 콘텐츠 페이지와 동일
        # (리뷰 finding #3: 미주입이면 mf/검색 복원 코드가 영영 안 돎).
        _it = (ARCHIVE_ROOT / "index.html").with_suffix(".html.tmp")
        _it.write_text(_inject_update_banner(_idx_html), encoding="utf-8")
        _it.replace(ARCHIVE_ROOT / "index.html")
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
    force_open: bool = False,
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
        m_open = " open" if (cur_month == this_month or force_open) else ""
        parts.append(
            f'<details class="month"{m_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_month_kr(cur_month)}</span>'
            f'<span class="count">{month_counts.get(cur_month, 0)} 건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
    return cur_month


def _month_close(parts: list[str], prev_month: str | None) -> None:
    """Close the final open month group (no-op if none was opened)."""
    if prev_month is not None:
        parts.append("</div></details>")


def _render_screen_manual() -> str:
    """조건부 스크리너 제목 옆 📖 설명서 (collapsible).

    내용은 stock_screener.METRICS / PRESETS 에서 자동 생성 — /screen list ·
    _HELP_TEXT §6 과 단일 소스(지표/프리셋 추가 시 하드코딩 목록 없이 자동
    동기, 사용자 2026-06-10 'help 참조해서 설명서 추가'). import 실패 시
    빈 문자열(graceful — 페이지는 설명서만 없이 정상)."""
    try:
        from bot.stock_screener import METRICS, PRESETS
    except Exception:
        return ""
    try:
        import html as _h

        def _metric_rows(source: str, n_alias: int) -> str:
            rows = []
            for m in METRICS.values():
                if m.source != source:
                    continue
                alias = "/".join(m.aliases[:n_alias])
                rows.append(
                    f'<div class="mrow"><b>{_h.escape(m.name)}</b>'
                    f' ({_h.escape(alias)}) — {_h.escape(m.desc)}</div>'
                )
            return "".join(rows)

        preset_rows = "".join(
            f'<div class="mrow"><code>/screen {_h.escape(slug)}</code>'
            f' — <b>{_h.escape(p["name"])}</b>: {_h.escape(p["desc"])}</div>'
            for slug, p in PRESETS.items()
        )
        return (
            '<details class="cs-help"><summary>📖 설명서</summary>'
            '<div class="cs-help-body">'
            '<h4>사용법 (텔레그램 /screen = 대시보드 실행 버튼 동일)</h4>'
            '<div class="mrow"><code>PER&lt;15 PBR&lt;1 배당수익률&gt;3</code> — KR (KOSPI+KOSDAQ 전 종목)</div>'
            '<div class="mrow"><code>us PER&lt;15 DIV&gt;2</code> — 앞에 <code>us</code> = US S&amp;P 500</div>'
            '<div class="mrow"><code>jp minervini</code> · <code>hk</code> · <code>cn</code> · <code>tw</code>'
            ' — 닛케이격/홍콩/본토/대만 (해외=시총·거래대금 상위 ~225)</div>'
            '<div class="mrow"><code>매출QoQ&gt;10 영업이익QoQ&gt;5</code> — 전분기 대비 성장 필터</div>'
            '<div class="mrow"><code>valueup</code> — 프리셋 이름만 입력</div>'
            '<h4>Phase 1 지표 (pykrx 벌크 — KR 전 종목 즉시)</h4>'
            + _metric_rows("pykrx", 3) +
            '<h4>Phase 2 지표 (yfinance — Phase 1 생존 종목만)</h4>'
            + _metric_rows("yfinance", 2) +
            '<h4>QoQ 지표 (전분기 대비 성장률, yfinance 분기 재무제표)</h4>'
            + _metric_rows("qoq", 2) +
            '<h4>추세 지표 (일봉 · 시총 상위 스캔 · KR=pykrx / 해외=yfinance)</h4>'
            + _metric_rows("tech", 2) +
            '<div class="mrow" style="opacity:.85">📈 <b>Minervini 추세 템플릿</b>'
            ' (<code>/screen minervini</code> 또는 <code>트렌드템플릿&gt;=1</code>) — 7조건 전부 충족 시 1:'
            ' ①종가&gt;150·200일선 ②150&gt;200일선 ③200일선 상승추세(22일전 대비)'
            ' ④50&gt;150&gt;200일선 ⑤종가&gt;50일선 ⑥52주저점 +30%↑ ⑦52주고점 −25% 이내.'
            ' 비용 bound 위해 <b>시총 상위 종목만 스캔</b>(대형주 우선이라 모멘텀 신흥주는'
            ' 적게 잡힐 수 있음) · 결과에 <code>🔎 추세 스캔 N·일봉 M·통과 K</code> 진단 표시.'
            ' <b>평일 16:30 KST 사전계산</b>(일봉 캐시 워밍)되어 그날 실행은 빠름;'
            ' 첫 콜드 실행은 데드라인 내 부분결과(재실행 시 캐시 누적).</div>'
            '<h4>프리셋</h4>'
            + preset_rows +
            '<h4>연산자 · 시장 · 비용</h4>'
            '<div class="mrow">연산자: <code>&gt; &lt; &gt;= &lt;= =</code> · '
            '시장: KR 기본 · <code>us</code>=S&amp;P500 · <code>jp</code>=닛케이격 · '
            '<code>hk</code>=홍콩 · <code>cn</code>=본토 · <code>tw</code>=대만 '
            '(해외=개별 조회 ~1-2분) · 비용 ₩0 (LLM 미사용) · 24h 캐시</div>'
            '</div></details>'
        )
    except Exception:
        return ""


def _render_screener_page(runs: list[dict], outcomes: dict, screen_archives: list[dict] | None = None) -> str:
    """Render screener.html — date-grouped run cards with Top-3 mini-tables
    showing 5/15/30d outcomes (alpha vs sector ETF). Self-contained HTML
    with embedded CSS matching NOAH dashboard dark-on-light palette.
    Optional *screen_archives* embeds conditional screener results at bottom."""
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
    _scr_count = len(screen_archives or [])
    _combined_runs = total_runs + _scr_count

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
    · <a href="screener_domains.html">🗂️ 도메인 목록</a>
  </div>
  <h1>📊 Screener — Archive</h1>
  <p class="sub">Bottleneck Screener (테마별 6-18M thesis) + 조건부 스크리너 (정량 필터, ₩0) 통합</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{_combined_runs}</div><div class="stat-l">총 실행 ({total_runs}+{_scr_count})</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost_krw:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost_krw:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost_krw:,.0f}</div><div class="stat-l">누적 비용</div></div>
    <div class="stat"><div class="stat-v">{total_picks}</div><div class="stat-l">Top-3 picks</div></div>
    <div class="stat"><div class="stat-v">{resolved_count}</div><div class="stat-l">1m resolved</div></div>
  </div>
""")

    # 실행 기록이 0건이어도 섹션 헤더 + 실행 버튼은 렌더 — 대시보드에서
    # 첫 실행을 시작할 수 있어야 하므로 (2026-06-10 실행 버튼 추가와 함께
    # 옛 early-return 제거). 빈 상태 안내는 각 섹션 status 라인이 담당.

    # ── Bottleneck Screener section header + search + 실행 ──
    parts.append(f"""
  <h2 style="margin:24px 0 8px">🔬 Bottleneck Screener
    <button id="t3-csv" type="button" class="csv-btn" title="모든 실행의 Master Table 전 종목을 CSV(엑셀)로 — 도메인·날짜·테마섹션·전체 후보(Master Table 없으면 Top-3 폴백)">📥 CSV</button>
  </h2>
  <p class="sub">테마별 다종목 idea generation · 6-18M thesis</p>
  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="검색 — 또는 실행할 도메인/자유어 입력 (예: bottleneck, 방산, 로봇, 액체냉각) → 실행" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
    <button id="scr-run" type="button" class="run-btn" title="입력 도메인으로 Bottleneck Screener 실행 — 텔레그램 /screener 와 동일 (비우면 기본 bottleneck)">실행</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 Bottleneck Screener 실행</p>
  <div id="scr-cmd-panel" class="cmd-panel" style="display:none"></div>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

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
        day_open = ""  # 날짜 접힘 기본(사용자 2026-06-11) — 월만 펼침
        day_count = len(by_date[date])
        parts.append(
            f'<details class="day"{day_open}>'
            f'<summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{day_count} 건</span>'
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

    # ── 조건부 스크리너 section (date-grouped, search, same pattern as Bottleneck) ──
    # 헤더·설명서·검색바·실행 버튼은 기록 0건이어도 항상 렌더 — 첫 실행을
    # 대시보드에서 시작할 수 있어야 함. 기록 카드만 _scr_list 존재 시.
    _scr_list = screen_archives or []
    parts.append(
        '<hr style="border:none;border-top:1px solid var(--border);margin:32px 0 24px">'
        '<h2 style="margin:0 0 8px">📊 조건부 스크리너'
        + _render_screen_manual() +
        '<button id="cs-csv" type="button" class="csv-btn" title="모든 조건부 스크리너 결과를 CSV(엑셀)로 — 조건·날짜·종목·시총·지표 포함">📥 CSV</button>'
        '</h2>'
        '<p class="sub">정량 조건으로 KR + US + JP + HK + CN + TW 종목 필터 (pykrx/yfinance, ₩0). '
        '텔레그램: <code>/screen PER&lt;15 PBR&lt;1</code> · <code>/screen us PER&lt;15</code> '
        '· <code>/screen jp minervini</code> · <code>/screen valueup</code> · <code>/screen list</code></p>'
        '<div class="search-bar">'
        '<input id="cs-search" type="text" placeholder="검색 — 또는 실행할 조건/프리셋 입력 (예: PER<15 PBR<1, us PER<15, valueup, minervini) → 실행" autocomplete="off" spellcheck="false">'
        '<button id="cs-clear" type="button" title="검색 초기화">초기화</button>'
        '<button id="cs-run" type="button" class="run-btn" title="입력 조건으로 조건부 스크리너 실행 — 텔레그램 /screen 과 동일 (₩0)">실행</button>'
        '</div>'
        f'<p id="cs-status" class="status-line">총 {len(_scr_list)}건의 조건부 스크리너 실행</p>'
        '<div id="cs-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>'
        '<style>'
        '.scr-det{margin:8px 0;padding:8px 12px;border:1px solid var(--border);border-radius:6px}'
        '.scr-det summary{cursor:pointer;font-size:14px;position:relative}'
        '.scr-del{background:none;border:none;cursor:pointer;font-size:14px;float:right;opacity:0.4}'
        '.scr-del:hover{opacity:1}'
        '.scr-tbl{width:100%;border-collapse:collapse;margin:10px 0 16px;font-size:13px}'
        '.scr-tbl th,.scr-tbl td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}'
        '.scr-tbl th{color:var(--fg-soft);font-weight:600;font-size:12px}'
        '</style>'
    )
    if _scr_list:
        import html as _shtml

        # Group by date → month
        _cs_by_date: dict[str, list] = defaultdict(list)
        for _sa in _scr_list:
            _cs_by_date[_sa.get("_date", "")].append(_sa)
        _cs_dates = sorted(_cs_by_date.keys(), reverse=True)

        _cs_prev_month = ""
        def _cs_month_open(p, month_str, count, is_open):
            p.append(
                f'<details class="month cs-month"{" open" if is_open else ""}>'
                f'<summary>📅 {month_str[:4]}년 {int(month_str[5:7])}월'
                f'<span class="cnt">{count} 건</span></summary>'
            )
        def _cs_month_close(p, prev):
            if prev:
                p.append('</details>')

        for _cs_d in _cs_dates:
            _cs_month = _cs_d[:7]
            _cs_day_items = _cs_by_date[_cs_d]
            if _cs_month != _cs_prev_month:
                _cs_month_close(parts, _cs_prev_month)
                _cs_month_count = sum(len(_cs_by_date[d]) for d in _cs_dates if d.startswith(_cs_month))
                _cs_month_open(parts, _cs_month, _cs_month_count, _cs_month == _today_kst_str[:7])
                _cs_prev_month = _cs_month

            # 첫 진입 시 날짜는 접힌 상태(사용자 2026-06-11) — 월만 펼쳐
            # 날짜 목록 보이고, 결과는 클릭해서 펼침.
            parts.append(
                f'<details class="day cs-day" data-date="{_shtml.escape(_cs_d)}">'
                f'<summary>📅 {_cs_d} <span class="cnt">{len(_cs_day_items)} 건</span></summary>'
            )

            for _sa in _cs_day_items:
                _raw_conds = _shtml.unescape(_sa.get("conditions_display", _sa.get("conditions", "")))
                _scr_conds = _shtml.escape(_raw_conds)
                _scr_ts = (_sa.get("ts") or "")[:16].replace("T", " ")
                _scr_hits = _sa.get("hit_count", 0)
                _scr_total = _sa.get("total_universe", 0)
                _scr_elapsed = _sa.get("elapsed_sec", 0)
                _scr_cached = " 💾캐시" if _sa.get("was_cached") else ""
                _scr_date = _sa.get("_date", "")
                _scr_file = _sa.get("_file", "")
                _scr_market = _sa.get("market", "KR")
                _search_hay = _raw_conds
                for _sh in (_sa.get("hits") or []):
                    _search_hay += " " + str(_sh.get("name", "")) + " " + str(_sh.get("ticker", ""))

                _scr_rows = ""
                for _sh in (_sa.get("hits") or []):
                    _sh_name = _shtml.escape(str(_sh.get("name", "")))
                    _sh_ticker = _shtml.escape(str(_sh.get("ticker", "")))
                    _sh_mkt = _shtml.escape(str(_sh.get("market", "")))
                    _sh_mcap = _sh.get("mcap")
                    # 시총 표기 — 텔레그램과 단일 소스(fmt_mcap_display, 시장별 통화).
                    from bot.stock_screener import fmt_mcap_display as _fmd
                    _sh_mcap_str = _fmd(_scr_market, _sh_mcap, bracket=False)
                    from bot.stock_screener import fmt_metric_value as _fmv
                    _sh_extras = []
                    for _ek, _ev in _sh.items():
                        if _ek in ("code", "ticker", "name", "market", "mcap", "price"):
                            continue
                        if _ev is not None:
                            _sh_extras.append(f"{_ek}:{_fmv(_ev)}" if isinstance(_ev, (int, float)) else f"{_ek}:{_ev}")
                    _sh_extra = _shtml.escape(" · ".join(_sh_extras[:5]))
                    _sh_name_cell = f"<b>{_sh_name}</b>" if _sh_name else f"<b>{_sh_ticker}</b>"
                    _scr_rows += (
                        f"<tr><td>{_sh_name_cell}</td>"
                        f"<td><code>{_sh_ticker}</code></td>"
                        f"<td class='muted'>{_sh_mkt}</td>"
                        f"<td style='text-align:right'>{_sh_mcap_str}</td>"
                        f"<td class='muted'>{_sh_extra}</td></tr>"
                    )
                # 시총 컬럼 헤더 — 시장별 통화단위(US=$M, 아시아=현지통화M, KR=억).
                from bot.stock_screener import _MKT_CCY as _scr_ccy_map
                if _scr_market == "US":
                    _scr_mcap_hdr = "시총($M)"
                elif _scr_market in _scr_ccy_map:
                    _scr_mcap_hdr = f"시총({_scr_ccy_map[_scr_market]}M)"
                else:
                    _scr_mcap_hdr = "시총(억)"
                if _scr_rows:
                    _scr_table = (
                        f"<table class='scr-tbl'><thead><tr><th>종목명</th><th>티커</th><th>시장</th>"
                        f"<th>{_scr_mcap_hdr}</th><th>지표</th></tr></thead>"
                        f"<tbody>{_scr_rows}</tbody></table>"
                    )
                else:
                    # 0종목 — 텔레그램처럼 명시(사용자 2026-06-11: 펼쳤는데
                    # 빈 카드면 깨진 것처럼 보임) + 추세 진단 note(0 원인 가시화,
                    # 사용자 2026-06-23).
                    _scr_note = _shtml.escape(str(_sa.get("note", "")))
                    _note_html = (f"<br>🔎 {_scr_note}" if _scr_note else "")
                    _scr_table = (
                        "<p class='muted' style='padding:10px 14px;margin:0'>"
                        "조건에 맞는 종목이 없습니다 "
                        f"(0종목 / {_scr_total:,}종목 스캔).{_note_html}</p>"
                    )
                _scr_mbadge = {"US": " 🇺🇸", "JP": " 🇯🇵", "HK": " 🇭🇰",
                               "CN_A": " 🇨🇳", "TW": " 🇹🇼"}.get(_scr_market, "")
                parts.append(
                    f"<details class='scr-det cs-card' data-date=\"{_shtml.escape(_scr_date)}\""
                    f" data-file=\"{_shtml.escape(_scr_file)}\""
                    f" data-imp-id=\"cs|{_shtml.escape(_scr_date)}|{_shtml.escape(_scr_file)}\""
                    f" data-search=\"{_shtml.escape(_search_hay)}\">"
                    f"<summary>▸ <b>{_scr_conds}</b>{_scr_mbadge} — {_scr_hits}종목/{_scr_total:,}종목{_scr_cached} "
                    f"<span class='muted'>{_scr_ts} · {_scr_elapsed:.1f}초</span>"
                    f"<button class='scr-del' type='button' title='삭제'>🗑️</button></summary>"
                    f"{_scr_table}</details>\n"
                )
            parts.append('</details>')  # close day
        _cs_month_close(parts, _cs_prev_month)

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
    if (q.charAt(0) === '/') {  // 명령 모드 — 필터 대신 콘솔 안내
      showCardsMode();
      if (sts) sts.textContent = '⌨️ 명령 모드 — Enter 또는 [실행] 으로 실행 (예: /usage · /screener_list · /portfolio)';
      return;
    }
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
    fetch(btn.dataset.api || 'api/screener_delete', {
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

document.querySelectorAll('.scr-del').forEach(function(btn) {
  btn.addEventListener('click', function(e) {
    e.stopPropagation(); e.preventDefault();
    var det = btn.closest('.scr-det');
    if (!det) return;
    if (!confirm('이 기록을 삭제할까요?')) return;
    var date = det.dataset.date;
    var file = det.dataset.file;
    fetch('api/screen_delete', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date: date, filename: file})
    }).then(function(r) { return r.json(); }).then(function(d) {
      if (d.ok) { det.style.opacity = '0.3'; setTimeout(function() { det.remove(); }, 400); }
      else { alert('삭제 실패: ' + (d.error || '')); }
    }).catch(function(err) { alert('삭제 실패: ' + err); });
  });
});

// 조건부 스크리너 검색
(function() {
  var csInp = document.getElementById('cs-search');
  var csClr = document.getElementById('cs-clear');
  var csSts = document.getElementById('cs-status');
  var csEmp = document.getElementById('cs-empty');
  if (!csInp) return;
  var csCards = Array.from(document.querySelectorAll('.cs-card'));
  var csDays = Array.from(document.querySelectorAll('.cs-day'));
  var csMonths = Array.from(document.querySelectorAll('.cs-month'));
  var csTotal = csCards.length;

  function csFilter() {
    var qraw = (csInp.value || '').trim();
    var q = qraw.toLowerCase();
    if (qraw.charAt(0) === '/') {  // 명령 모드 — 필터 안 함
      csCards.forEach(function(c) { c.style.display = ''; });
      csDays.forEach(function(d) { d.style.display = ''; });
      csMonths.forEach(function(m) { m.style.display = ''; });
      csEmp.style.display = 'none';
      csSts.textContent = '⌨️ 명령 모드 — Enter 또는 [실행] 으로 실행 (예: /usage · /screener_list)';
      return;
    }
    if (!q) {
      csCards.forEach(function(c) { c.style.display = ''; });
      csDays.forEach(function(d) { d.style.display = ''; });
      csMonths.forEach(function(m) { m.style.display = ''; });
      csEmp.style.display = 'none';
      csSts.textContent = '총 ' + csTotal + '건의 조건부 스크리너 실행';
      return;
    }
    var hits = 0;
    csCards.forEach(function(c) {
      var hay = (c.dataset.search || '').toLowerCase();
      var show = hay.indexOf(q) >= 0;
      c.style.display = show ? '' : 'none';
      if (show) hits++;
    });
    csDays.forEach(function(d) {
      var any = d.querySelector('.cs-card:not([style*="display: none"])');
      d.style.display = any ? '' : 'none';
      if (any) d.open = true;
    });
    csMonths.forEach(function(m) {
      var any = m.querySelector('.cs-day:not([style*="display: none"])');
      m.style.display = any ? '' : 'none';
      if (any) m.open = true;
    });
    csEmp.style.display = hits === 0 ? 'block' : 'none';
    csSts.textContent = hits + '건 매칭 (검색: "' + csInp.value.trim() + '")';
  }
  csInp.addEventListener('input', csFilter);
  csClr.addEventListener('click', function() { csInp.value = ''; csFilter(); csInp.focus(); });
})();
</script>
<script>""" + _CONSOLE_JS + r"""
// Bottleneck Screener 실행(텔레그램 /screener) · '/' 입력 = 명령 콘솔.
noahConsoleSetup({
  inputId: 'scr-search', btnId: 'scr-run', statusId: 'scr-status', panelId: 'scr-cmd-panel',
  runKind: 'screener', requireQuery: false,
  defaultLabel: 'bottleneck (기본)',
  confirmText: 'Bottleneck Screener 를 실행할까요?\n· 소요 5-10분 · 비용 ~₩300-500 (오늘 캐시 시 무료)\n· 결과: 텔레그램 채널 + 이 페이지(자동 갱신)',
  okMsg: 'Screener 요청 접수 — 5-10분 후 텔레그램 채널과 이 페이지에 게시됩니다.'
});
// 조건부 스크리너 실행(텔레그램 /screen, ₩0) · '/' 입력 = 명령 콘솔.
noahConsoleSetup({
  inputId: 'cs-search', btnId: 'cs-run', statusId: 'cs-status', panelId: 'scr-cmd-panel',
  runKind: 'screen', requireQuery: true,
  emptyMsg: '실행할 조건 또는 프리셋을 입력하세요.\n예: PER<15 PBR<1 · us PER<15 · valueup · minervini · 트렌드템플릿>=1\n뒤에 fresh 붙이면 캐시 무시 강제 재실행 (예: minervini fresh)\n(전체 지표는 제목 옆 📖 설명서)\n또는 / 명령 (예: /usage · /screener_list)',
  confirmText: '조건부 스크리너를 실행할까요?\n· KR 수초~수십초 · us 는 ~1-2분 · 비용 ₩0\n· 결과: 텔레그램 채널 + 이 페이지(자동 갱신)',
  okMsg: '조건부 스크리너 요청 접수 — 결과는 텔레그램 채널과 이 페이지에 게시됩니다.'
});
</script>
<script>
/* 📥 CSV 내보내기 (사용자 2026-06-13 '스크리너 두개도 엑셀') — Bottleneck Top-3 +
   조건부 스크리너 결과를 클라이언트사이드 CSV(엑셀 UTF-8 BOM). 서버 endpoint 불요. */
(function(){
  function noahCsv(filename, header, rows){
    var esc=function(v){return '"'+(v==null?'':(''+v)).replace(/"/g,'""')+'"';};
    var out=[header.map(esc).join(',')];
    rows.forEach(function(r){out.push(r.map(esc).join(','));});
    var blob=new Blob(["﻿"+out.join('\r\n')],{type:'text/csv;charset=utf-8;'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download=filename;document.body.appendChild(a);a.click();
    setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},120);
  }
  function txt(el){return el?(el.textContent||'').replace(/\s+/g,' ').trim():'';}
  function stamp(){var d=new Date(),p=function(n){return(''+n).padStart(2,'0');};
    return d.getFullYear()+p(d.getMonth()+1)+p(d.getDate());}
  var t3=document.getElementById('t3-csv');
  if(t3)t3.addEventListener('click',function(){
    // 전체 = 각 실행 Master Table 의 **종목별 상세** 파싱 (사용자 2026-06-13 '이런것
    // 까지 전체 CSV'). 포맷: '[섹션] · Tier · Ticker · (시장) · Company' 헤더 + 그
    // 아래 '• 필드: 값' 불릿(Tier A/B/C·가격반영도·Catalyst·Kill Trigger). 시장
    // (시장)에 내부 '·'(뉴욕·미국)가 있어 괄호 인지 정규식 사용. |표/Top-3 폴백.
    var rows=[];
    var H=['날짜','도메인','테마섹션','티어','티커','시장','회사',
           'TierA(컨센서스PT)','TierB(실적/펀더)','TierC(기타)','가격반영도','Catalyst+시기','KillTrigger'];
    document.querySelectorAll('.card').forEach(function(card){
      var domain=txt(card.querySelector('.domain')), date=card.getAttribute('data-date')||'';
      var before=rows.length;
      var mt=card.querySelector('.analysis-b[data-section="master_table"]');
      if(mt){
        var cur=null;
        var flush=function(){ if(cur){rows.push([date,domain,cur.sec,cur.tier,cur.tk,cur.mkt,cur.co,
          cur.tA,cur.tB,cur.tC,cur.price,cur.cat,cur.kill]); cur=null;} };
        (mt.textContent||'').split('\n').forEach(function(ln){
          ln=ln.trim(); if(!ln)return;
          var h=ln.match(/^\[([^\]]+)\]\s*·\s*(.+)$/);            // [섹션] · 나머지
          if(h){
            flush();
            var m=h[2].match(/^(.+?)\s*·\s*(.+?)\s*·\s*\((.+?)\)\s*·\s*(.+)$/);  // Tier·Ticker·(시장)·Company
            if(m) cur={sec:h[1].trim(),tier:m[1].trim(),tk:m[2].trim(),mkt:m[3].trim(),co:m[4].trim(),
                       tA:'',tB:'',tC:'',price:'',cat:'',kill:''};
            else { var p=h[2].split('·').map(function(s){return s.trim();});
                   cur={sec:h[1].trim(),tier:p[0]||'',tk:p[1]||'',mkt:'',co:p.slice(2).join(' · '),
                        tA:'',tB:'',tC:'',price:'',cat:'',kill:''}; }
            return;
          }
          if(!cur)return;
          var kv=ln.replace(/^[•\-]\s*/,'').match(/^(.+?)\s*[:：]\s*(.+)$/); if(!kv)return;
          var k=kv[1], v=kv[2].trim();
          if(/Tier\s*A|컨센서스/i.test(k))cur.tA=v; else if(/Tier\s*B|실적|펀더/i.test(k))cur.tB=v;
          else if(/Tier\s*C|기타/i.test(k))cur.tC=v; else if(/가격|반영/.test(k))cur.price=v;
          else if(/Catalyst|촉매|시기/i.test(k))cur.cat=v; else if(/Kill|트리거/i.test(k))cur.kill=v;
        });
        flush();
      }
      if(rows.length===before){                                  // ·/• 없음 → |표 또는 Top-3 폴백
        var any=false;
        if(mt)(mt.textContent||'').split('\n').forEach(function(ln){
          ln=ln.trim(); if(ln.indexOf('|')<0)return;
          var c=ln.split('|').map(function(s){return s.trim();}).filter(Boolean);
          if(c.length&&!c.every(function(x){return /^[-:\s]+$/.test(x);})){rows.push([date,domain,'(표)'].concat(c.slice(0,10)));any=true;}
        });
        if(!any){var tb=card.querySelector('table.picks'); if(tb)tb.querySelectorAll('tbody tr').forEach(function(tr){
          rows.push([date,domain,'Top-3'].concat(Array.prototype.map.call(tr.querySelectorAll('td'),txt).slice(0,10)));});}
      }
    });
    if(!rows.length){alert('내보낼 데이터가 없습니다.');return;}
    noahCsv('screener_전체_'+stamp()+'.csv',H,rows);
  });
  var cs=document.getElementById('cs-csv');
  if(cs)cs.addEventListener('click',function(){
    var rows=[];
    document.querySelectorAll('table.scr-tbl').forEach(function(tb){
      var det=tb.closest('.scr-det');
      var date=det?(det.getAttribute('data-date')||''):'';
      var cond=det?txt(det.querySelector('summary')).slice(0,80):'';
      tb.querySelectorAll('tbody tr').forEach(function(tr){
        var c=tr.querySelectorAll('td'); if(c.length<5)return;
        rows.push([date,cond,txt(c[0]),txt(c[1]),txt(c[2]),txt(c[3]),txt(c[4])]);
      });
    });
    if(!rows.length){alert('내보낼 조건부 스크리너 결과가 없습니다.');return;}
    noahCsv('screener_조건_'+stamp()+'.csv',
      ['날짜','조건','종목명','티커','시장','시총','지표'],rows);
  });
})();
</script>
""" + _imp_cfg("screener", card_sel=".card, .cs-card", head_sel="summary", search_sel="#scr-search, #cs-search") + _IMPORTANT_BLOCK + """
</body></html>
""")
    return "".join(parts)


_SCREENER_CSS = (
    """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Screener — Archive</title>
<script>""" + _THEME_JS + """</script>
<style>
/* 📥 CSV 내보내기 버튼 (사용자 2026-06-13) — h2 제목 옆 작은 버튼 */
.csv-btn{margin-left:10px;padding:4px 10px;font-size:12px;font-weight:600;vertical-align:middle;
  border:1px solid var(--border,#d0d7de);border-radius:6px;background:var(--card,#f6f8fa);
  color:var(--text,#1f2328);cursor:pointer}
.csv-btn:hover{background:#3b82f6;color:#fff;border-color:#3b82f6}
/* Time-based light/dark theme (Asia/Seoul) — _THEME_JS toggles
   `data-theme="dark"` on the <html> element between 19:00-07:00.
   Mirrors the NOAH index/detail pages. Variables below adapt: light
   defaults at `:root`, dark overrides at `:root[data-theme="dark"]`. */
:root {
  --bg:#f7f8f9; --card:#ffffff; --border:#e8e8ea;
  --text:#282a30; --muted:#8a8f98; --accent:#5e6ad2;
  --pos:#059669; --neg:#dc2626; --neu:#8a8f98; --pending:#d97706;
  --accent-soft:rgba(94,106,210,0.07);
  --accent-soft2:rgba(94,106,210,0.14);
  --surface-tint:rgba(0,0,0,0.05);
  --surface-tint-strong:rgba(0,0,0,0.07);
  --row-border:rgba(0,0,0,0.05);
  --tier-l-bg:rgba(94,106,210,0.12); --tier-l-fg:#4651b8;
  --tier-m-bg:rgba(16,185,129,0.14); --tier-m-fg:#047857;
  --tier-s-bg:rgba(245,158,11,0.18); --tier-s-fg:#b45309;
  --search-btn-bg:#16a34a; --search-btn-hover:#15803d;
  --mark-bg:rgba(245,158,11,0.45); --mark-fg:#7c2d12;
  --mark-target-bg:rgba(245,158,11,0.7); --mark-target-fg:#1f2937;
}
:root[data-theme="dark"] {
  --bg:#0b0c0e; --card:#141518; --border:#26272b;
  --text:#e2e3e6; --muted:#8a8f98; --accent:#7c84e8;
  --pos:#10B981; --neg:#EF4444; --neu:#6B7280; --pending:#F59E0B;
  --accent-soft:rgba(124,132,232,0.06);
  --accent-soft2:rgba(124,132,232,0.15);
  --surface-tint:rgba(255,255,255,0.04);
  --surface-tint-strong:rgba(255,255,255,0.06);
  --row-border:rgba(255,255,255,0.04);
  --tier-l-bg:rgba(124,132,232,0.15); --tier-l-fg:#9aa2f0;
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
.search-bar button.run-btn { background:#3b82f6; }
.search-bar button.run-btn:hover { background:#2563eb; }
.search-bar button:disabled { opacity:0.55; cursor:wait; }
.cs-help { display:inline-block; font-size:13px; font-weight:400; margin-left:10px;
  vertical-align:middle; position:relative; }
.cs-help > summary { cursor:pointer; color:var(--accent); list-style:none;
  user-select:none; }
.cs-help > summary::-webkit-details-marker { display:none; }
.cs-help .cs-help-body { position:static; background:var(--card);
  border:1px solid var(--border); border-radius:10px; padding:14px 16px;
  margin:10px 0 4px; font-size:13px; line-height:1.7; color:var(--text);
  max-width:860px; }
.cs-help .cs-help-body h4 { margin:10px 0 4px; font-size:13px; }
.cs-help .cs-help-body h4:first-child { margin-top:0; }
.cs-help .cs-help-body code { background:var(--surface-tint); padding:1px 5px;
  border-radius:4px; font-family:'IBM Plex Mono',monospace; font-size:12px; }
.cs-help .cs-help-body .mrow { color:var(--muted); }
.cs-help .cs-help-body .mrow b { color:var(--text); font-weight:600; }
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
        # L4 = 각 L3 아래 세부 sub-industry (2026-06-14, GICS sub-industry 깊이).
        # 예: Semiconductors → Memory/Foundry/Equipment/Logic AI ...
        ("L4_SUBINDUSTRY", "🎯 L4 Sub-industry", "각 L3 아래 세부 sub-industry (slug/별칭 접근)"),
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
        "L4_SUBINDUSTRY": "l4",
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
        "L4_SUBINDUSTRY": False,
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
.layer-section.l4 {{ border-left-color:#3b82f6; }}
.layer-section.l4 {{ background:linear-gradient(90deg,
  rgba(59,130,246,0.07) 0%, transparent 280px); }}
.layer-section.ad_hoc {{ border-left-color:#f59e0b; }}
.layer-section.ad_hoc {{ background:linear-gradient(90deg,
  rgba(245,158,11,0.08) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l1 {{
  background:linear-gradient(90deg, rgba(59,130,246,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l2 {{
  background:linear-gradient(90deg, rgba(16,185,129,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l3 {{
  background:linear-gradient(90deg, rgba(167,139,250,0.10) 0%, transparent 280px); }}
:root[data-theme="dark"] .layer-section.l4 {{
  background:linear-gradient(90deg, rgba(96,165,250,0.10) 0%, transparent 280px); }}
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
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
    · <a href="screener.html">📊 Screener</a>
    · <a href="gics_candidates.html">🧬 GICS 후보</a>
  </div>
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
        # Load conditional screener archives for merged section
        _screen_archives: list[dict] = []
        try:
            from bot.stock_screener import load_screen_archives
            _screen_archives = load_screen_archives()
        except Exception:
            pass
        html = _render_screener_page(runs, outcomes, screen_archives=_screen_archives)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "screener.html").write_text(html, encoding="utf-8")
        log.info("dashboard: screener.html regenerated (%d runs, %d outcomes, %d screen)",
                 len(runs), len(outcomes), len(_screen_archives))
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

# 중요(important) 마크 — 카드별 ★ 토글 + 검색창 옆 '중요만' 필터 (사용자 2026-06-26).
# 서버 저장(bot.important_marks, GET/POST /api/important) → 모바일·데스크탑 동기화.
# 전 대시보드 universal: 자기완결 <style>+<script> 블록 1개(_IMPORTANT_BLOCK)를 각
# 페이지에 주입하고, 페이지는 window.IMP_CFG(또는 IMP_SURFACE 만)로 표면·셀렉터만 선언.
# 블록 JS 가 카드마다 ★ 버튼 + 검색창 옆 필터 버튼을 자동 주입(템플릿 편집 불요).
# 기본값 = scr-search 카드 표면(daily_byte 류): cardSel='.card', headSel='.card-h',
# idAttrs=['date','filename'], searchSel='#scr-search'. 다른 표면은 IMP_CFG 로 덮어씀.
# ★ 클릭 = 토글+POST(낙관적, 실패 시 복구). 필터 = html.imp-only → 비중요 카드 숨김
# (CSS !important 로 기존 검색/필터와 합성: 검색 통과 ∧ 중요, 둘 다 만족해야 표시).
# 재렌더 뷰(NOAH/밸류체인)는 rebuild 후 window.__impApply() 호출로 ★ 재주입·상태복원.
_IMPORTANT_BLOCK = """
<style>
.imp-ctl{cursor:pointer;background:none;border:0;padding:0 3px;font-size:14px;line-height:1;color:#9aa2ad;vertical-align:middle;flex:0 0 auto}
.imp-ctl:hover{color:#f5c518}
.imp-star.on{color:#f5c518}
.memo-btn.on{color:#34c759}
.rem-btn.on{color:#ff9f0a}
.imp-filter-btn,.memo-filter-btn{cursor:pointer;background:var(--surface,#1c1c1e);color:var(--text,#e8e8ea);border:1px solid var(--border,#333);border-radius:14px;padding:5px 11px;font-size:12px;white-space:nowrap;margin-left:4px}
.imp-filter-btn.active{background:#f5c518;color:#111;border-color:#f5c518;font-weight:600}
.memo-filter-btn.active{background:#34c759;color:#111;border-color:#34c759;font-weight:600}
.memo-panel{display:none;margin:6px 0 4px;padding:2px}
.memo-panel.open{display:block}
.memo-ta{width:100%;min-height:54px;box-sizing:border-box;font:13px/1.45 inherit;padding:6px;border:1px solid var(--border,#444);border-radius:6px;background:var(--surface,#111);color:var(--text,#eee);resize:vertical}
.memo-bar{display:flex;align-items:center;gap:8px;margin-top:4px}
.memo-save{cursor:pointer;background:#34c759;color:#111;border:0;border-radius:6px;padding:4px 12px;font-size:12px;font-weight:600}
.memo-del{cursor:pointer;background:none;color:#ff453a;border:1px solid #ff453a;border-radius:6px;padding:4px 12px;font-size:12px}
.memo-st{font-size:11px;color:#9aa2ad}
.rem-panel{display:none;margin:6px 0 4px;padding:2px}
.rem-panel.open{display:block}
.rem-bar{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.rem-lab{font-size:12px;color:var(--text-sub,#9aa2ad)}
.rem-sep{color:var(--text-sub,#9aa2ad);opacity:.5;margin:0 2px}
.rem-time,.rem-date{font:13px inherit;padding:5px 8px;border:1px solid var(--border,#444);border-radius:6px;background:var(--surface,#111);color:var(--text,#eee);color-scheme:dark light}
.rem-date{width:110px}
.rem-set{cursor:pointer;background:#ff9f0a;color:#111;border:0;border-radius:6px;padding:4px 12px;font-size:12px;font-weight:600}
.rem-clr{cursor:pointer;background:none;color:#9aa2ad;border:1px solid var(--border,#555);border-radius:6px;padding:4px 12px;font-size:12px}
.rem-st{font-size:11px;color:#7aa7ff}
html.imp-only .imp-markable:not(.imp-on){display:none!important}
html.memo-only .imp-markable:not(.has-memo){display:none!important}
/* 필터 시 마크 카드 없는 날짜/월 그룹(details)은 숨김 — 카드 details(.imp-markable)는 제외.
   :has() 미지원 브라우저는 그룹이 남되 그룹 펼침(JS)으로 마크 카드는 보임(graceful). */
html.imp-only details:not(.imp-markable):not(:has(.imp-markable.imp-on)){display:none!important}
html.memo-only details:not(.imp-markable):not(:has(.imp-markable.has-memo)){display:none!important}
/* div 기반 접기 그룹(DART .df-month/.df-date-group 등)도 마크 없으면 숨김 */
html.imp-only .df-month:not(:has(.imp-markable.imp-on)),html.imp-only .df-date-group:not(:has(.imp-markable.imp-on)){display:none!important}
html.memo-only .df-month:not(:has(.imp-markable.has-memo)),html.memo-only .df-date-group:not(:has(.imp-markable.has-memo)){display:none!important}
/* 필터 중엔 접힘(.collapsed)이어도 그룹 본문 강제 노출(마크 카드 보이게) */
html.imp-only .df-month-body,html.imp-only .df-date-body,html.memo-only .df-month-body,html.memo-only .df-date-body{display:block!important}
</style>
<script>
// ★ 중요 + 📝 메모 + ⏰ 알람 — 서버(/api/important·/api/memo·/api/reminder) 단일 저장,
// 기기 무관 동기화. 로드 시 GET /api/important 로 marks+memos+reminders 1회 수신.
(function(){
  if(window.__impInit) return; window.__impInit=true;
  var C=window.IMP_CFG||{};
  var SURFACE=window.IMP_SURFACE||C.surface||'';
  var CARDSEL=C.cardSel||'.card', HEADSEL=C.headSel||'.card-h';
  var IDATTRS=C.idAttrs||['date','filename'], SEARCHSEL=C.searchSel||'#scr-search';
  var TIME_RE=/^(\\d{1,2})\\.(\\d{1,2})\\.(\\d{1,2}):(\\d{2})$/;   // MM.DD.HH:MM (KST)
  var MARKS=new Set(), MEMOS={}, REMS={};
  function idOf(card){
    if(card.dataset.impId) return card.dataset.impId;
    return IDATTRS.map(function(a){return card.getAttribute('data-'+a)||'';}).join('|');
  }
  function api(path,body){ return fetch(path,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(r){return r.json();}); }
  function cardText(card){
    var c=card.cloneNode(true);
    c.querySelectorAll('.imp-ctl,.memo-panel,.rem-panel,.del-btn,.scr-del').forEach(function(e){e.remove();});
    return (c.innerText||c.textContent||'').replace(/\\n{3,}/g,'\\n\\n').trim();
  }
  function mkbtn(cls,glyph,title){ var b=document.createElement('button');
    b.className='imp-ctl '+cls; b.type='button'; b.textContent=glyph; b.title=title; return b; }
  function paint(card){
    var id=idOf(card);
    var on=MARKS.has(id), hm=!!MEMOS[id], hr=!!(REMS[id]&&REMS[id].active);
    card.classList.toggle('imp-on',on);
    card.classList.toggle('has-memo',hm);
    card.classList.toggle('has-rem',hr);
    var s=card.querySelector('.imp-star'); if(s){s.classList.toggle('on',on); s.textContent=on?'★':'☆';}
    var m=card.querySelector('.memo-btn'); if(m){m.classList.toggle('on',hm);}
    var r=card.querySelector('.rem-btn'); if(r){r.classList.toggle('on',hr);
      r.title=hr?('알람 '+REMS[id].time+' (KST) — 클릭하여 변경/해제'):'알람 설정 (HH:MM, KST)';}
  }
  // 버튼 3종 템플릿 — 카드마다 createElement×3 대신 clone 1회(생성 비용↓).
  var _TPL=null;
  function _tpl(){
    if(!_TPL){ _TPL=document.createDocumentFragment();
      _TPL.appendChild(mkbtn('imp-star','☆','중요 표시 토글'));
      _TPL.appendChild(mkbtn('memo-btn','📝','메모'));
      _TPL.appendChild(mkbtn('rem-btn','⏰','알람 설정 (HH:MM, KST)'));
    }
    return _TPL;
  }
  function ensure(){
    if(!SURFACE) return;
    // 배치화(성능 2026-07-03, 사용자 '③도 적용'): 카드 수천 개 페이지에서
    // 버튼 삽입을 300개 단위 idle 청크로 나눠 첫 페인트 블로킹/버벅임 완화.
    // 멱등(재호출 시 .imp-star 존재 카드는 clone 안 붙이고 paint 만) — 청크
    // 진행 중 재호출돼도 안전. counts 는 마지막 청크 후 1회.
    var cards=Array.prototype.slice.call(document.querySelectorAll(CARDSEL));
    var i=0;
    function chunk(){
      var end=Math.min(i+300,cards.length);
      for(;i<end;i++){ var card=cards[i];
        card.classList.add('imp-markable');
        var head=(HEADSEL&&card.querySelector(HEADSEL))||card;
        if(!head.querySelector('.imp-star')) head.appendChild(_tpl().cloneNode(true));
        paint(card);
      }
      if(i<cards.length){
        if(window.requestIdleCallback) requestIdleCallback(chunk,{timeout:200});
        else setTimeout(chunk,0);
      } else counts();
    }
    chunk();
  }
  function counts(){
    var ni=document.querySelectorAll('.imp-markable.imp-on').length;
    var nm=document.querySelectorAll('.imp-markable.has-memo').length;
    document.querySelectorAll('.imp-filter-btn').forEach(function(f){f.title='중요 표시한 항목만 ('+ni+')';});
    document.querySelectorAll('.memo-filter-btn').forEach(function(f){f.title='메모 있는 항목만 ('+nm+')';});
  }
  function injectInto(into){
    if(into&&!into.querySelector('.imp-filter-btn')){
      into.appendChild(mkbtn2('imp-filter-btn','⭐ 중요'));
      into.appendChild(mkbtn2('memo-filter-btn','📝 메모'));
    }
  }
  function injectFilter(){
    // filterInto(검색창 없는 표면) 우선, 아니면 SEARCHSEL 의 모든 검색창 옆(한 페이지
    // 검색창 여러 개 — 예 스크리너 #scr-search·#cs-search — 전부에 주입).
    var done=false;
    if(C.filterInto){ var fi=document.querySelector(C.filterInto); if(fi){ injectInto(fi); done=true; } }
    if(!done&&SEARCHSEL){
      document.querySelectorAll(SEARCHSEL).forEach(function(inp){ if(inp.parentNode) injectInto(inp.parentNode); });
    }
  }
  function expandGroups(sel){
    // 마크된 카드가 든 그룹을 펼쳐 마크 카드 노출 — details 기반 + div 기반(.collapsed,
    // 예 DART .df-month/.df-date-group) 둘 다. 카드 자신(details)은 제외.
    document.querySelectorAll('details').forEach(function(d){
      if(d.matches&&d.matches(CARDSEL)) return;
      if(d.querySelector(sel)) d.open=true;
    });
    document.querySelectorAll('.df-month,.df-date-group,.day,.cs-day').forEach(function(g){
      if(g.querySelector(sel)) g.classList.remove('collapsed');
    });
  }
  function mkbtn2(cls,txt){ var b=document.createElement('button'); b.className=cls;
    b.type='button'; b.textContent=txt; return b; }
  function openMemo(card){
    var id=idOf(card), panel=card.querySelector('.memo-panel');
    if(!panel){
      panel=document.createElement('div'); panel.className='memo-panel';
      var ta=document.createElement('textarea'); ta.className='memo-ta';
      ta.placeholder='내 생각 메모... (비우고 저장 = 삭제)'; ta.value=MEMOS[id]||'';
      var bar=document.createElement('div'); bar.className='memo-bar';
      var save=document.createElement('button'); save.type='button'; save.className='memo-save'; save.textContent='저장';
      var del=document.createElement('button'); del.type='button'; del.className='memo-del'; del.textContent='삭제';
      var stt=document.createElement('span'); stt.className='memo-st';
      bar.appendChild(save); bar.appendChild(del); bar.appendChild(stt);
      panel.appendChild(ta); panel.appendChild(bar);
      var head=(HEADSEL&&card.querySelector(HEADSEL));
      if(head&&head.nextSibling) card.insertBefore(panel,head.nextSibling); else card.appendChild(panel);
      function commit(text){
        stt.textContent='저장 중...';
        api('api/memo',{surface:SURFACE,id:id,text:text}).then(function(res){
          if(res&&res.ok){ if(text.trim()) MEMOS[id]=text.trim(); else delete MEMOS[id];
            paint(card); counts();
            stt.textContent=text.trim()?'저장됨 ✓':'삭제됨 ✓'; setTimeout(function(){stt.textContent='';},1500);
          } else { stt.textContent='실패'; }
        }).catch(function(){ stt.textContent='실패'; });
      }
      function doSave(){ commit(ta.value); }
      function doDel(){
        // 저장된 메모 없음(빈 창) → 삭제할 내용 없으니 창만 닫기. 저장분 있을 때만 삭제.
        if(!MEMOS[id]){ panel.classList.remove('open'); return; }
        if(!confirm('이 메모를 삭제할까요?')) return;
        ta.value=''; commit('');
      }
      save.addEventListener('click',doSave); ta.addEventListener('blur',doSave);
      del.addEventListener('click',doDel);
    }
    panel.classList.toggle('open');
    if(panel.classList.contains('open')){
      if(card.tagName==='DETAILS') card.open=true;
      var t=panel.querySelector('textarea'); if(t) t.focus();
    }
  }
  function openRem(card){
    // 인라인 알람 패널 — 2모드: ⏰매일(type=time, 매일 반복) · 📅특정일(MM.DD.HH:MM,
    // 그 날짜부터). 각 [설정], 공용 [해제]. 모든 시각 KST. 본문은 설정 시 스냅샷 전송.
    var id=idOf(card), panel=card.querySelector('.rem-panel');
    if(!panel){
      panel=document.createElement('div'); panel.className='rem-panel';
      var bar=document.createElement('div'); bar.className='rem-bar';
      var l1=document.createElement('span'); l1.className='rem-lab'; l1.textContent='⏰ 매일';
      var tin=document.createElement('input'); tin.type='time'; tin.className='rem-time';
      var tset=document.createElement('button'); tset.type='button'; tset.className='rem-set'; tset.textContent='설정';
      var sep=document.createElement('span'); sep.className='rem-sep'; sep.textContent='·';
      var l2=document.createElement('span'); l2.className='rem-lab'; l2.textContent='📅 특정일';
      var din=document.createElement('input'); din.type='text'; din.className='rem-date'; din.placeholder='06.26.04:30'; din.setAttribute('inputmode','numeric');
      var dset=document.createElement('button'); dset.type='button'; dset.className='rem-set'; dset.textContent='설정';
      var clr1=document.createElement('button'); clr1.type='button'; clr1.className='rem-clr'; clr1.textContent='해제';
      var clr=document.createElement('button'); clr.type='button'; clr.className='rem-clr'; clr.textContent='해제';
      var stt=document.createElement('span'); stt.className='rem-st';
      // 매일 [설정][해제] · 특정일 [설정][해제] (해제는 둘 다 동일하게 알람 종료)
      [l1,tin,tset,clr1,sep,l2,din,dset,clr,stt].forEach(function(e){bar.appendChild(e);});
      panel.appendChild(bar);
      var head=(HEADSEL&&card.querySelector(HEADSEL));
      if(head&&head.nextSibling) card.insertBefore(panel,head.nextSibling); else card.appendChild(panel);
      function refresh(){
        var r=REMS[id];
        if(r&&r.active&&r.time){
          if(r.time.indexOf('.')<0){ tin.value=r.time; din.value=''; stt.textContent='매일 '+r.time+' KST'; }
          else { din.value=r.time; tin.value=''; stt.textContent='특정일 '+r.time+' KST'; }
        } else { stt.textContent=''; }
      }
      refresh();
      function save(t){
        stt.textContent='저장 중...';
        api('api/reminder',{surface:SURFACE,id:id,time:t,on:true,memo:MEMOS[id]||'',card:cardText(card)})
          .then(function(res){ if(res&&res.ok&&res.active){ REMS[id]={time:res.time,active:true};
            paint(card); refresh(); } else { stt.textContent='실패'; } }).catch(function(){ stt.textContent='실패'; });
      }
      tset.addEventListener('click',function(){ if(!tin.value){alert('매일 알람 시각을 선택해줘');return;} save(tin.value); });
      dset.addEventListener('click',function(){ var v=(din.value||'').trim();
        if(!TIME_RE.test(v)){ alert('특정일 형식: MM.DD.HH:MM (예 06.26.04:30)'); return; } save(v); });
      function doClear(){
        // 설정된 알람 없음(빈 창) → 해제할 내용 없으니 창만 닫기. 활성 알람 있을 때만 해제.
        if(!(REMS[id]&&REMS[id].active)){ panel.classList.remove('open'); return; }
        stt.textContent='해제 중...';
        api('api/reminder',{surface:SURFACE,id:id,time:'',on:false}).then(function(res){
          if(res&&res.ok){ delete REMS[id]; tin.value=''; din.value=''; paint(card);
            stt.textContent='해제됨 ✓'; setTimeout(function(){stt.textContent='';},1500); }
          else { stt.textContent='실패'; } }).catch(function(){ stt.textContent='실패'; });
      }
      clr1.addEventListener('click',doClear); clr.addEventListener('click',doClear);
    }
    panel.classList.toggle('open');
    if(panel.classList.contains('open')&&card.tagName==='DETAILS') card.open=true;
  }
  window.__impApply=function(){ ensure(); };
  injectFilter();
  fetch('api/important').then(function(r){return r.json();}).then(function(d){
    if(d){ if(d.marks&&d.marks[SURFACE]) MARKS=new Set(d.marks[SURFACE]);
      if(d.memos&&d.memos[SURFACE]) MEMOS=d.memos[SURFACE];
      if(d.reminders&&d.reminders[SURFACE]) REMS=d.reminders[SURFACE]; }
  }).catch(function(){}).then(ensure);
  document.addEventListener('click',function(e){
    var st=e.target.closest&&e.target.closest('.imp-star');
    if(st){ e.preventDefault(); e.stopPropagation();
      var card=st.closest(CARDSEL); if(!card) return;
      var id=idOf(card), on=!MARKS.has(id);
      if(on) MARKS.add(id); else MARKS.delete(id); paint(card); counts();
      api('api/important',{surface:SURFACE,id:id,on:on})
        .then(function(res){ if(!res||!res.ok) revert(); }).catch(revert);
      function revert(){ if(on) MARKS.delete(id); else MARKS.add(id); paint(card); counts();
        alert('중요 저장 실패 — 다시 시도해줘'); }
      return; }
    var mb=e.target.closest&&e.target.closest('.memo-btn');
    if(mb){ e.preventDefault(); e.stopPropagation();
      var c1=mb.closest(CARDSEL); if(c1) openMemo(c1); return; }
    var rb=e.target.closest&&e.target.closest('.rem-btn');
    if(rb){ e.preventDefault(); e.stopPropagation();
      var c2=rb.closest(CARDSEL); if(c2) openRem(c2); return; }
    var f=e.target.closest&&e.target.closest('.imp-filter-btn');
    if(f){ e.preventDefault();
      var a1=document.documentElement.classList.toggle('imp-only');
      document.querySelectorAll('.imp-filter-btn').forEach(function(x){x.classList.toggle('active',a1);});
      if(a1) expandGroups('.imp-markable.imp-on');
      // 페이지별 그룹-카운트 필터(예 DART applyFilters)가 교집합 재적용하도록 통지.
      document.dispatchEvent(new CustomEvent('impfilterchange')); return; }
    var g=e.target.closest&&e.target.closest('.memo-filter-btn');
    if(g){ e.preventDefault();
      var a2=document.documentElement.classList.toggle('memo-only');
      document.querySelectorAll('.memo-filter-btn').forEach(function(x){x.classList.toggle('active',a2);});
      if(a2) expandGroups('.imp-markable.has-memo');
      document.dispatchEvent(new CustomEvent('impfilterchange')); return; }
  });
})();
</script>
"""


def _imp_cfg(surface: str, card_sel: str = "", head_sel: str = "",
             id_attrs: list[str] | None = None, search_sel: str = "",
             filter_into: str = "") -> str:
    """페이지에 중요-마크 설정(window.IMP_CFG) 주입 — _IMPORTANT_BLOCK 앞에 둘 것.
    기본값(scr-search 카드 표면)과 같으면 surface 만 줘도 됨. filter_into = 검색창이
    없는 표면에서 ⭐중요 필터 버튼을 직접 넣을 컨테이너 셀렉터."""
    import json as _j
    cfg = {"surface": surface}
    if card_sel:
        cfg["cardSel"] = card_sel
    if head_sel:
        cfg["headSel"] = head_sel
    if id_attrs:
        cfg["idAttrs"] = id_attrs
    if search_sel:
        cfg["searchSel"] = search_sel
    if filter_into:
        cfg["filterInto"] = filter_into
    return f"<script>window.IMP_CFG={_j.dumps(cfg, ensure_ascii=False)};</script>"


_DAILY_BYTE_JS = """
<script>
// 딥링크 — market.html '전체 보기' 가 #card-... 해시로 진입하면 해당 카드의
// 월/일/카드 details 요소를 모두 펼치고 스크롤(바로 해당 글이 열림). 매칭
// 카드 없으면 무동작(다른 아카이브 페이지에서도 안전).
(function() {
  function openHash() {
    var id = (location.hash || '').slice(1);
    if (!id) return;
    var el = document.getElementById(id);
    if (!el) return;
    var p = el;
    while (p) { if (p.tagName === 'DETAILS') { p.open = true; } p = p.parentElement; }
    try { el.scrollIntoView({ block: 'start' }); } catch (e) {}
  }
  window.addEventListener('hashchange', openHash);
  if (document.readyState !== 'loading') { openHash(); }
  else { document.addEventListener('DOMContentLoaded', openHash); }
  setTimeout(openHash, 60);
})();
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
  if (!inp) return;
  // 과거 월 lazy(details.month[data-lazy], 성능 2026-07-03 — index 패턴 미러,
  // 이 JS 를 공유하는 레딧·블로그·Daily Byte 페이지 공통 적용) — 로드 시 재수집.
  let cards = [], dayGroups = [], monthGroups = [], cardData = [];
  const total = parseInt(sts.dataset.total || '0', 10) || document.querySelectorAll('.card').length;
  const MAX_SNIPPETS = 80;
  function rescan() {
    cards = Array.from(document.querySelectorAll('.card'));
    dayGroups = Array.from(document.querySelectorAll('details.day'));
    monthGroups = Array.from(document.querySelectorAll('details.month'));
    cardData = cards.map(function(c) {
      let lines = [];
      try { lines = JSON.parse(c.dataset.lines || '[]'); } catch (e) {}
      const t = c.querySelector('.domain');
      const titleTxt = t ? t.textContent.trim() : '';
      if (titleTxt) lines.unshift({sec: 'title', txt: titleTxt});
      return {card: c, lines: lines};
    });
  }
  rescan();
  const lazyMonths = Array.from(document.querySelectorAll('details.month[data-lazy]'));
  function pendingMonths(){ return lazyMonths.filter(function(m){ return !m.__loaded && !m.__failed; }); }
  function failedCount(){ return lazyMonths.filter(function(m){ return m.__failed; }).length; }
  function loadMonth(m){
    if (m.__loaded) return Promise.resolve();
    if (m.__loading) return m.__loading;
    var body = m.querySelector('.month-body');
    m.__loading = fetch(m.dataset.lazy, {cache:'no-store'})
      .then(function(r){ if(!r.ok) throw 0; return r.text(); })
      .then(function(html){
        body.innerHTML = html; m.__loaded = true; m.__failed = false;
        try { rescan(); if (window.__impApply) window.__impApply(); } catch(e) {}
      })
      .catch(function(){
        m.__loading = null; m.__failed = true;
        body.innerHTML = '<div style="color:var(--fg-soft);padding:10px 4px">불러오기 실패 — 접었다 다시 펼쳐보세요.</div>';
      });
    return m.__loading;
  }
  function loadAllMonths(){ return Promise.all(pendingMonths().map(loadMonth)); }
  lazyMonths.forEach(function(m){
    m.addEventListener('toggle', function(){
      if (m.open && !m.__loaded) { m.__failed = false; loadMonth(m).then(applyFilter); }
    });
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
    if (pendingMonths().length) {
      sts.textContent = '🔎 전체 기록 불러오는 중… (' + pendingMonths().length + '개월)';
      loadAllMonths().then(applyFilter);
      return;
    }
    showSnippetsMode(q);
    var fc = failedCount();
    if (fc) sts.textContent += ' · ⚠️ ' + fc + '개월 로드 실패(해당 월 제외)';
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

document.addEventListener('click', function(ev) {
    const btn = ev.target.closest && ev.target.closest('.del-btn');
    if (!btn) return;
    ev.stopPropagation(); ev.preventDefault();
    const card = btn.closest('.card'); if (!card) return;
    const date = card.dataset.date; const filename = card.dataset.filename;
    if (!date || !filename) return;
    if (!confirm('📰 ' + date + ' / ' + filename + ' Daily Byte 기록을 삭제할까요?')) return;
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
</script>
""" + _IMPORTANT_BLOCK + """
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


# ── 자동 업데이트 배너 (사용자 2026-06-29) — 켜놓은 화면에 새 내용이 생기면 알림.
# 메인/ASIA 처럼 영역 swap 이 아니라, HEAD 로 Last-Modified 만 폴링(본문 0·새 엔드
# 포인트 0)해 페이지 파일이 바뀌면 "🆕 새 업데이트" 배너 → 클릭 시 reload. 콘텐츠
# 도착형 페이지에만 주입. swap 안 하는 이유: 이 페이지들엔 필터·검색·펼친카드·스크롤·
# 중요/메모 상태가 있어 innerHTML 교체 시 날아감 → 배너(알림+1클릭)로 상태 보존.
# 비용: HEAD(헤더만)·탭 숨김 skip·LLM/regen 0 → 단일 사용자엔 사실상 무부하.
_UPDATE_BANNER_JS = """<script>
(function(){
  if(window.__updBanner) return; window.__updBanner=true;
  // 갱신 UX(사용자 2026-07-03 스펙):
  // ① 기준선 — 30분마다 새 버전 있으면 **자동 반영**(스크롤·검색어·시장필터
  //   저장→복원 = 보던 자리 그대로. 입력 중이면 그 틱 스킵 → 다음 체크에 재시도).
  // ② 그 사이 — 1분마다 조용히 HEAD 체크, 새 버전이면 하단 팝업으로 선택:
  //   [바로 반영]=즉시(상태 복원) / [그대로 보기]=유지(같은 버전 재팝업 없음,
  //   단 30분 기준선 도달 시 자동 반영).
  var CHECK=60000, BASELINE=1800000;
  var url=location.pathname+location.search, base=null, ignored=null;
  var loadedAt=Date.now(), SK='updst:'+location.pathname;
  var lastKey=0;
  document.addEventListener('keydown', function(){ lastKey=Date.now(); }, true);
  function typing(){ var a=document.activeElement;
    // 포커스만으론 차단 금지(검색창 클릭 후 방치 시 기준선이 영영 안 돌던 것,
    // 리뷰 finding #6) — 최근 60초 내 실제 키 입력이 있을 때만 '입력 중'.
    return !!(a && (a.tagName==='INPUT'||a.tagName==='TEXTAREA'||a.isContentEditable)
              && Date.now()-lastKey < 60000); }
  function saveState(){
    try{
      var inputs={};
      document.querySelectorAll('input[type=text][id],input[type=search][id],input:not([type])[id]').forEach(
        function(i){ if(i.value) inputs[i.id]=i.value; });
      var mf=document.querySelector('.mf-btn.active');
      sessionStorage.setItem(SK, JSON.stringify(
        {y:window.scrollY, inputs:inputs, mf:mf?mf.dataset.mf:null, ts:Date.now()}));
    }catch(e){}
  }
  function restoreState(){
    try{
      var raw=sessionStorage.getItem(SK); if(!raw) return;
      sessionStorage.removeItem(SK);
      var st=JSON.parse(raw);
      if(!st || Date.now()-(st.ts||0) > 300000) return;   // 5분 지난 상태는 폐기
      Object.keys(st.inputs||{}).forEach(function(id){
        var el=document.getElementById(id);
        if(el){ el.value=st.inputs[id];
          el.dispatchEvent(new Event('input',{bubbles:true})); } });
      if(st.mf){ var b=document.querySelector('.mf-btn[data-mf="'+st.mf+'"]');
        if(b) b.click(); }
      var y=st.y||0;
      [80,300,900].forEach(function(d){ setTimeout(function(){window.scrollTo(0,y);},d); });
    }catch(e){}
  }
  function apply(){ saveState(); location.reload(); }
  function head(){ return fetch(url,{method:'HEAD',cache:'no-store'})
    .then(function(r){ return r.ok?r.headers.get('Last-Modified'):null; })
    .catch(function(){ return null; }); }
  function banner(cur){
    if(document.getElementById('upd-banner')) return;
    var b=document.createElement('div'); b.id='upd-banner';
    b.style.cssText='position:fixed;left:50%;bottom:18px;transform:translateX(-50%);'+
      'z-index:9999;background:#2563eb;color:#fff;padding:10px 10px 10px 18px;border-radius:22px;'+
      'font-size:14px;font-weight:600;box-shadow:0 4px 16px rgba(0,0,0,.35);display:flex;gap:8px;align-items:center;flex-wrap:wrap';
    var t=document.createElement('span'); t.textContent='🆕 새 업데이트 도착 — 바로 반영할까요?';
    var y=document.createElement('span'); y.textContent='바로 반영';
    y.style.cssText='cursor:pointer;background:rgba(255,255,255,.22);padding:4px 12px;border-radius:14px';
    y.onclick=apply;
    var n=document.createElement('span'); n.textContent='그대로 보기';
    n.style.cssText='cursor:pointer;opacity:.8;padding:4px 10px';
    n.title='하던 작업 그대로 — 이 버전은 다시 알리지 않음(30분 기준선에선 자동 반영)';
    n.onclick=function(){ ignored=cur; b.remove(); };
    b.appendChild(t); b.appendChild(y); b.appendChild(n);
    document.body.appendChild(b);
  }
  function tick(){ if(document.hidden) return;
    head().then(function(v){ if(!v) return;
      if(base===null){ base=v; return; }
      if(v===base) return;
      if(Date.now()-loadedAt>=BASELINE){          // ① 30분 기준선 = 자동 반영
        if(typing()) return;                       //   입력 중 → 이 틱 스킵
        apply(); return; }
      if(v!==ignored) banner(v);                   // ② 사이 = 팝업으로 선택
    }); }
  head().then(function(v){ if(base===null) base=v; });   // 로드 시점 = 보고 있는 버전
  restoreState();
  setInterval(tick, CHECK);
})();
</script>"""


def _inject_update_banner(html: str) -> str:
    """콘텐츠 도착형 페이지 HTML 첫 </body> 앞에 자동 업데이트 배너 주입. 멱등(이미
    주입됐거나 </body> 없으면 그대로). 순수."""
    if not html or "__updBanner" in html or "</body>" not in html:
        return html
    return html.replace("</body>", _UPDATE_BANNER_JS + "</body>", 1)


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
    weekly_n = sum(1 for r in runs if r.get("kind") in ("weekly", "us_weekly"))

    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
  </div>
  <h1>📰 Daily Byte — Archive</h1>
  <p class="sub">장 마감 후 시장 브리프 · 🇰🇷 19:00 / 🇺🇸 08:00 / 📅 Weekly 22:00 (KST) · 수급·시황 관찰(교육·정보), 투자 권유 아님</p>

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
            f'<span class="count">{month_count} 건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
        for date in month_dates:
            day_open = ""
            day_count = len(by_date[date])
            _dst.append(
                f'<details class="day"{day_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count} 건</span>'
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
                is_us = kind == "us_daily"
                kind_badge = ("📅 한국 Weekly" if is_weekly
                              else "📅 미국 Weekly" if kind == "us_weekly"
                              else "🇺🇸 미국 Daily" if is_us
                              else "🇰🇷 한국 Daily")
                title = f"{kind_badge} · {_html.escape(date)}"
                # Body is already Telegram-safe HTML (<b>/<i> only) from
                # daily_kr_flow._post_process. Strip the leading title line
                # (Python adds '📰 <b>Daily Byte - ...</b>' + <i>subtitle</i>)
                # so the card doesn't double up on the heading.
                body = (r.get("body") or "").strip()
                # 수평선/구분선 줄 제거 (render 시점 — strip-fix 이전에 아카이브된
                # 옛 run 의 '---' / '--- / ---' 도 소급 정리). 단어문자 없이 대시류
                # 2+ 만 있는 줄 + 그로 인한 연속 빈 줄.
                body = re.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
                # 면책/디스클레이머 줄 제거 (사용자 정책 2026-06-11 — 옛
                # 아카이브 기록 소급 정리, _post_process 백스톱과 동일 패턴).
                body = re.sub(r"(?m)^(?=.*투자\s*권유)(?=.*아(?:님|닙)).*$", "", body)
                body = re.sub(r"(?m)^\s*(?:<[^>]+>)*\s*면책.*$", "", body)
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
          <span class="domain">{title} ({ts} · ₩{cost:,.1f} · {elapsed:.0f}s)</span>
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
    parts.append(_imp_cfg("daily_byte"))
    parts.append(_DAILY_BYTE_JS)
    return "".join(parts)


def regenerate_daily_byte_index() -> None:
    """Scan daily_byte archive → write daily_byte.html under ARCHIVE_ROOT.
    Called from daily_kr_flow after a run + from _periodic_dashboard_refresh.
    All errors swallowed."""
    try:
        runs = _load_daily_byte_runs()
        html = _inject_update_banner(_render_daily_byte_page(runs))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "daily_byte.html").write_text(html, encoding="utf-8")
        log.info("dashboard: daily_byte.html regenerated (%d runs)", len(runs))
    except Exception as exc:
        log.warning("dashboard: daily_byte regen failed: %s", exc)


# ── 부동산 Byte archive view ──────────────────────────────────────────────
_REALESTATE_ARCHIVE_DIR = Path.home() / ".tradingagents" / "realestate_archive"
_REALESTATE_JS = (_DAILY_BYTE_JS
    .replace("Daily Byte 브리프", "부동산/청약")
    .replace("Daily Byte 기록", "부동산/청약 기록")
    .replace("fetch('api/daily_byte_delete',", "fetch(card.dataset.delApi || 'api/realestate_delete',"))


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
    <a href="market.html">🌍 홈</a>
  </div>
  <h1>🏠 부동산 — Archive</h1>
  <p class="sub">아파트 실거래가(MOLIT) 주간 브리프 + 청약홈 분양 피드 · ticker·5거래일과 별개 · 공공데이터 관찰(투자 권유 아님)</p>
  <div class="stats">
    <div class="stat"><div class="stat-v">{total}</div><div class="stat-l">총 기록</div></div>
    <div class="stat"><div class="stat-v">₩{today_cost:,.0f}</div><div class="stat-l">오늘 비용</div></div>
    <div class="stat"><div class="stat-v">₩{month_cost:,.0f}</div><div class="stat-l">이번 달</div></div>
    <div class="stat"><div class="stat-v">₩{total_cost:,.0f}</div><div class="stat-l">누적 비용</div></div>
  </div>
  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="지역 / 단지명 / 실거래 / 청약 검색 (예: 강남, 평당, 금리, 전세, 동탄)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total}건의 부동산/청약 기록</p>
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
    _first_day = True
    _first_card = False
    for date in sorted(by_date.keys(), reverse=True):
        _prev_month = _month_transition(
            parts, _prev_month, date, _this_month, _month_counts,
            force_open=_first_day)
        day_open = ""  # 부동산 날짜 접힘 기본(사용자 2026-06-11) — 월만 펼침
        _first_day = False
        parts.append(
            f'<details class="day"{day_open}><summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{len(by_date[date])} 건</span></summary>'
            f'<div class="day-body">')
        for r in by_date[date]:
            body = (r.get("body") or "").strip()
            body = _re_r.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body)
            kind = r.get("_kind", "realestate")
            del_api = "api/cheongyak_delete" if kind == "cheongyak" else "api/realestate_delete"
            if kind == "cheongyak":
                cnt = r.get("count", 0) or 0
                title = f"🎟️ 신규 분양 {cnt}건 · {_html.escape(date)}"
            else:
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
  <details class="card"{" open" if _first_card else ""} id="{card_id}" data-date="{_html.escape(r.get('_date',''))}" data-filename="{filename}" data-del-api="{del_api}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if _first_card else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">{title} ({_html.escape(ts_clock)} · ₩{cost:,.1f})</span>
      <button class="del-btn" type="button" title="이 기록 삭제">🗑️</button>
    </summary>
    <div class="card-body">
      {img_html}
      <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body}</div></div>
    </div>
  </details>
""")
            _first_card = False
        parts.append('</div></details>')
    _month_close(parts, _prev_month)
    parts.append("</div>")
    parts.append(_imp_cfg("realestate"))
    parts.append(_REALESTATE_JS)
    return "".join(parts)


def regenerate_realestate_index() -> None:
    """Scan realestate + cheongyak archives → write realestate.html under ARCHIVE_ROOT."""
    try:
        re_runs = _load_realestate_runs()
        ch_runs = _load_cheongyak_runs()
        for r in re_runs:
            r.setdefault("_kind", "realestate")
        for r in ch_runs:
            r["_kind"] = "cheongyak"
        runs = re_runs + ch_runs
        html = _inject_update_banner(_render_realestate_page(runs))
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
    <a href="market.html">🌍 홈</a>
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
        parts.append(
            f'<details class="day"><summary class="day-head">'
            f'<span>📅 {_html.escape(date)}</span>'
            f'<span class="count">{len(by_date[date])} 건</span></summary>'
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
      <span class="domain">{title} ({_html.escape(ts_clock)} · ₩{cost:,.1f})</span>
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
    parts.append(_imp_cfg("cheongyak"))
    parts.append(_CHEONGYAK_JS)
    return "".join(parts)


def regenerate_cheongyak_index() -> None:
    """Cheongyak merged into realestate page — redirect."""
    regenerate_realestate_index()


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
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
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
  <p id="scr-status" class="status-line" data-total="{total_runs}">총 {total_runs}건의 포워드</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    fragments: dict[str, str] = {}   # 과거 월 lazy(ri_m_*.html, 성능 2026-07-03)
    if not runs:
        parts.append("""
  <div class="empty">
    아직 포워드 기록이 없습니다. t.me/insidertracking 채널의 '미국 레딧 게시물 분석' 게시물이 올라오면 5분 내 자동 수집됩니다.
  </div>
</div></body></html>""")
        return "".join(parts), fragments

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

    _sorted_months_ri = sorted(months.keys(), reverse=True)
    for _mi_ri, month in enumerate(_sorted_months_ri):
        month_dates = months[month]
        month_count = sum(len(by_date[d]) for d in month_dates)
        # 최신 달(또는 이번 달)만 인라인 — 과거 달은 프래그먼트 lazy(성능
        # 2026-07-03, index 패턴). mb 버퍼에 본문을 모아 목적지 분기.
        _inline_ri = (_mi_ri == 0 or month == _this_month_ri)
        _head_ri = (f'<summary class="month-head">'
                    f'<span>📆 {_html.escape(_format_month_ri(month))}</span>'
                    f'<span class="count">{month_count} 건</span>'
                    f'</summary>')
        mb: list = []
        _dst = parts if _inline_ri else mb
        if _inline_ri:
            parts.append(f'<details class="month" open>{_head_ri}'
                         f'<div class="month-body">')
        for date in month_dates:
            # 날짜 그룹 기본 접힘 (사용자 2026-06-12 '다른 대시보드처럼' —
            # Daily Byte 전부-접힘 패턴 mirror)
            day_open = ""
            day_count = len(by_date[date])
            _dst.append(
                f'<details class="day"{day_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count} 건</span>'
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
                card_default_open = False   # 전부 접힘 (2026-06-12)
                card_open_attr = " open" if card_default_open else ""
                card_id = (
                    f"card-{_html.escape(r.get('_date', ''))}-{filename}"
                    .replace(".", "_")
                )

                _dst.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date', ''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">📨 {title} ({ts_html})</span>
    </summary>
    <div class="card-body">
      <div class="analysis-sec"><div class="analysis-b" data-section="brief">{body_html}</div></div>
    </div>
  </details>
""")
            _dst.append('</div></details>')  # close day
        if _inline_ri:
            parts.append('</div></details>')  # close month
        else:
            _frag_ri = f"ri_m_{month}.html"
            fragments[_frag_ri] = "".join(mb)
            parts.append(
                f'<details class="month" data-lazy="{_frag_ri}">{_head_ri}'
                f'<div class="month-body"><div style="color:var(--fg-soft);'
                f'padding:10px 4px">펼치면 불러옵니다… ({month_count} 건)</div>'
                f'</div></details>')

    parts.append("</div>")
    parts.append(_imp_cfg("reddit"))
    parts.append(_DAILY_BYTE_JS)
    return "".join(parts), fragments


def regenerate_reddit_insider_index() -> None:
    """Scan reddit_insider archive → write reddit_insider.html under
    ARCHIVE_ROOT. Called from bot.reddit_insider_watch after new forwards
    + from periodic dashboard refresh + on startup."""
    try:
        runs = _load_reddit_insider_runs()
        _ri_html, _ri_frags = _render_reddit_insider_page(runs)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        for _fn, _fc in _ri_frags.items():   # 프래그먼트 먼저(404 창 차단)+원자
            try:
                _ft = (ARCHIVE_ROOT / _fn).with_suffix(".tmp")
                _ft.write_text(_fc, encoding="utf-8")
                _ft.replace(ARCHIVE_ROOT / _fn)
            except Exception as _fe:
                log.warning("dashboard: reddit fragment %s failed: %s", _fn, _fe)
        _rt = (ARCHIVE_ROOT / "reddit_insider.html").with_suffix(".html.tmp")
        _rt.write_text(_inject_update_banner(_ri_html), encoding="utf-8")
        _rt.replace(ARCHIVE_ROOT / "reddit_insider.html")
        log.info(
            "dashboard: reddit_insider.html regenerated (%d runs)",
            len(runs),
        )
    except Exception as exc:
        log.warning("dashboard: reddit_insider regen failed: %s", exc)


# ── 블로그 Watcher 대시보드 (blog.html, 2026-06-11) ─────────────────────
# bot/blog_watch.py 가 네이버 '변화하는 기업을 찾아서' 새 글을 채널 포워드
# + ~/.tradingagents/blog_archive/ 에 JSON 적층. 본 페이지는 그 아카이브를
# 레딧 페이지(reddit_insider.html)와 동일 형식으로 surface (사용자 2026-06-11
# — 2026-05-31 '대시보드 없음' 정책 변경). nav 노출은 홈 + NOAH 두 곳만.
_BLOG_ARCHIVE_DIR = Path.home() / ".tradingagents" / "blog_archive"


def _load_blog_runs() -> list[dict]:
    """Scan ~/.tradingagents/blog_archive/YYYY-MM-DD/*.json → newest-first.
    각 dict: {ts, date, title, link, summary, desc, _date, _filename}."""
    import json as _json
    runs: list[dict] = []
    if not _BLOG_ARCHIVE_DIR.exists():
        return runs
    try:
        for date_dir in sorted(_BLOG_ARCHIVE_DIR.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for json_file in sorted(date_dir.iterdir(), reverse=True):
                if not json_file.name.endswith(".json"):
                    continue
                try:
                    with open(json_file, encoding="utf-8") as f:
                        rec = _json.load(f)
                    rec["_path"] = str(json_file)
                    rec["_date"] = rec.get("date") or date_dir.name
                    rec["_filename"] = json_file.name
                    runs.append(rec)
                except Exception as exc:
                    log.warning("dashboard: blog load %s failed: %s",
                                json_file, exc)
    except Exception as exc:
        log.warning("dashboard: blog scan failed: %s", exc)
    return runs


def _kg_candidate_cost_usd(kinds=None) -> tuple[float, float]:
    """관계후보(kg) LLM 비용 — trade usage.jsonl 에서 (today, month) USD 합산.
    kinds=세트 주면 그 kind 만(소스별 대시보드 카드: 블로그=kg_blog/legacy,
    DART=kg_dart). 미지정 시 전 kg_* (메인 관계후보 버킷과 동일). 메인 합산
    (_compute_stats 관계후보)과 동일 소스·동일 분리. graceful((0.0, 0.0))."""
    import json as _j
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now_kst = datetime.datetime.now(kst)
    today_str = now_kst.strftime("%Y-%m-%d")
    month_prefix = now_kst.strftime("%Y-%m")
    _trade_dir = os.environ.get("TRADE_DATA_DIR", "").strip()
    path = (Path(_trade_dir) / "usage.jsonl" if _trade_dir
            else Path.home() / ".trade" / "usage.jsonl")
    _kset = set(kinds) if kinds is not None else None
    today_usd = month_usd = 0.0
    if not path.exists():
        return (0.0, 0.0)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = _j.loads(line)
                except Exception:
                    continue
                _k = rec.get("kind") or ""
                if _kset is not None:
                    if _k not in _kset:
                        continue
                elif not _k.startswith("kg"):
                    continue
                _cu = rec.get("cost_usd")
                if _cu is not None and _cu > 0:
                    cu = float(_cu)
                else:
                    _ck = rec.get("cost_krw", 0) or 0
                    cu = float(_ck) / 1330.0 if _ck > 0 else 0.0
                if cu <= 0:
                    continue
                _rd = rec.get("date")
                if isinstance(_rd, str) and _rd:
                    day = _rd
                else:
                    _ts = rec.get("ts")
                    if not _ts:
                        continue
                    try:
                        day = datetime.datetime.fromtimestamp(
                            float(_ts), kst).strftime("%Y-%m-%d")
                    except Exception:
                        continue
                if day.startswith(month_prefix):
                    month_usd += cu
                    if day == today_str:
                        today_usd += cu
    except Exception as exc:
        log.warning("dashboard: kg cost read failed: %s", exc)
    return (today_usd, month_usd)


def _load_kg_candidates(limit: int = 60) -> list[dict]:
    """레퍼런스북 관계후보 승인 큐(trade.kg_candidates 런타임 CSV) → 최신순 dict 목록.
    각 dict: {company,relation,target,evidence,source,date,status}. graceful([])."""
    import csv as _csv
    out: list[dict] = []
    try:
        from trade import kg_candidates as _kg
        p = _kg._candidates_csv_path()
        if not p.exists():
            return out
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = _csv.reader(f)
            next(r, None)                                  # 헤더
            for row in r:
                if len(row) < 7:
                    continue
                out.append({"company": row[0], "relation": row[1],
                            "target": row[2], "evidence": row[3],
                            "source": row[4], "date": row[5], "status": row[6]})
    except Exception as exc:
        log.warning("dashboard: kg_candidates load failed: %s", exc)
        return []
    out.reverse()                                          # 최신 적재가 위로
    return out[:limit]


def _render_kg_candidates_section(cands: list[dict]) -> str:
    """블로그 페이지 '🔗 관계후보' 섹션 — 대기('후보') 표 + '✅ 처리됨'(승인/등재)
    접이식 분리(사용자 2026-06-24 옵션B). 운영자 검토 전용(자동 등재 없음)."""
    import html as _h
    waiting = [c for c in cands if c.get("status", "후보") == "후보"]
    done = [c for c in cands if c.get("status", "후보") in ("승인", "등재")]
    if not waiting and not done:
        return ""
    n_wait, n_done = len(waiting), len(done)
    # 등재=취급품목→레퍼런스북 반영(초록), 승인=납품/고객 등 기록만(회색), 후보=대기(amber).
    _badge = {"후보": "#a16207", "승인": "#6b7280", "등재": "#15803d"}

    def _row(c, with_btn):
        co = _h.escape(c.get("company", ""))
        rel = _h.escape(c.get("relation", ""))
        tgt = _h.escape(c.get("target", ""))
        ev = _h.escape((c.get("evidence", "") or "")[:120])
        src = _h.escape((c.get("source", "") or "").replace("blog:", "").replace("dart:", ""))
        dt = _h.escape(c.get("date", ""))
        st = c.get("status", "후보")
        col = _badge.get(st, "#a16207")
        if with_btn:
            _aco = _h.escape(c.get("company", ""), quote=True)
            _arel = _h.escape(c.get("relation", ""), quote=True)
            _atgt = _h.escape(c.get("target", ""), quote=True)
            last = (f'<button class="kg-apply" data-co="{_aco}" data-rel="{_arel}" '
                    f'data-tgt="{_atgt}">반영</button>')
        else:
            last = (f'<span style="background:{col};color:#fff;border-radius:8px;'
                    f'padding:1px 8px;font-size:11px">{_h.escape(st)}</span>')
        return (f'<tr><td><b>{co}</b></td><td style="color:var(--muted)">{rel}</td>'
                f'<td>{tgt}</td><td style="color:var(--muted);font-size:12px">{ev}</td>'
                f'<td style="color:var(--muted);font-size:12px">{src}</td>'
                f'<td style="font-size:12px">{dt}</td><td>{last}</td></tr>')

    _head_th = ('<thead><tr style="text-align:left;border-bottom:1px solid var(--border)">'
                '<th>회사</th><th>관계</th><th>대상</th><th>근거</th><th>출처</th>'
                '<th>추출일</th><th>반영</th></tr></thead>')
    wait_tbody = ("".join(_row(c, True) for c in waiting)
                  or '<tr><td colspan="7" style="color:var(--muted);padding:8px">반영 대기 후보가 없습니다 — 새 글·공시가 오면 채워집니다.</td></tr>')
    done_rows = "".join(_row(c, False) for c in done)
    applyall = (
        f'<div style="margin:0 0 10px"><button id="kg-apply-all" style="background:#15803d;color:#fff;border:0;border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer">✅ 전체반영 ({n_wait}건)</button>'
        f'<span id="kg-apply-msg" style="margin-left:10px;font-size:12px;color:var(--muted)"></span></div>'
    ) if waiting else ''
    done_block = (
        f'<details class="day" style="margin-top:12px"><summary class="day-head" style="cursor:pointer">'
        f'<span>✅ 처리됨 <span style="color:var(--muted);font-weight:400;font-size:12px">(등재=레퍼런스북 반영 · 승인=기록만 · {n_done}건)</span></span></summary>'
        f'<div style="overflow-x:auto;padding:8px 0"><table style="width:100%;border-collapse:collapse;font-size:13px">'
        f'{_head_th}<tbody>{done_rows}</tbody></table></div></details>'
    ) if done else ''
    return f"""
  <details class="month" style="margin:6px 0 14px" open>
    <summary class="month-head">
      <span>🔗 관계후보 <span style="color:var(--muted);font-weight:400;font-size:12px">(블로그·DART공시 자동발굴 · 반영 대기 {n_wait}건)</span></span>
      <span class="count">검토 큐</span>
    </summary>
    <div class="month-body" style="padding:10px 12px">
      <p style="color:var(--muted);font-size:12px;margin:0 0 8px;line-height:1.6">
        새 블로그 글·DART 계약공시 본문에서 자동 추출한 <b>(회사)–(관계)–(대상)</b> 후보입니다.
        ⛔ 자동 등재 안 함 — <b>반영</b> 버튼으로 승인하세요. <b>취급품목</b>은 수출입 레퍼런스북에
        즉시 반영(중복 자동 스킵), 그 외 관계(납품/고객/계열)는 '승인' 기록만(현재 소비 surface 없음).
      </p>
      {applyall}
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          {_head_th}
          <tbody>{wait_tbody}</tbody>
        </table>
      </div>
      {done_block}
    </div>
  </details>
  <style>
    .kg-apply{{background:#2563eb;color:#fff;border:0;border-radius:7px;padding:3px 12px;font-size:12px;cursor:pointer}}
    .kg-apply:disabled{{opacity:.5;cursor:default}}
  </style>
  <script>
  (function(){{
    function post(url, body){{
      return fetch(url, {{method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify(body||{{}})}}).then(function(r){{return r.json();}});
    }}
    var msg = document.getElementById('kg-apply-msg');
    function say(t){{ if(msg) msg.textContent = t; }}
    document.querySelectorAll('.kg-apply').forEach(function(b){{
      b.addEventListener('click', function(){{
        b.disabled = true; b.textContent = '반영중…';
        post('api/kg_approve', {{company:b.dataset.co, relation:b.dataset.rel, target:b.dataset.tgt}})
          .then(function(j){{
            if(j && j.ok){{ b.textContent = j.ingested? '등재' : '승인';
              say('반영됨 (등재 '+(j.ingested||0)+' · 승인 '+(j.approved||0)+'). 새로고침하면 처리됨으로 이동합니다.'); }}
            else {{ b.disabled=false; b.textContent='반영'; say('실패: '+((j&&j.error)||'?')); }}
          }}).catch(function(e){{ b.disabled=false; b.textContent='반영'; say('오류: '+e); }});
      }});
    }});
    var all = document.getElementById('kg-apply-all');
    if(all) all.addEventListener('click', function(){{
      if(!confirm('대기 중인 관계후보를 전부 반영할까요?\\n취급품목은 수출입 레퍼런스북에 즉시 반영됩니다.')) return;
      all.disabled = true; all.textContent = '반영중…';
      post('api/kg_approve_all', {{}}).then(function(j){{
        if(j && j.ok){{ say('전체반영 완료 — 등재 '+(j.ingested||0)+' · 승인 '+(j.approved||0)+'. 새로고침합니다…');
          setTimeout(function(){{ location.reload(); }}, 900); }}
        else {{ all.disabled=false; all.textContent='✅ 전체반영'; say('실패: '+((j&&j.error)||'?')); }}
      }}).catch(function(e){{ all.disabled=false; all.textContent='✅ 전체반영'; say('오류: '+e); }});
    }});
  }})();
  </script>
"""


def _blog_desc_html(text: str) -> str:
    """블로그 본문 텍스트 → 문단 단위 <p> HTML (사용자 2026-06-16 '문맥단위로
    띄어쓰기 가독성'). 빈 줄/줄바꿈으로 문단을 분리해 각 문단에 아래 여백 +
    넉넉한 줄간격을 줌 — 옛 기록(단일 \\n 문단)·신규 기록(블록경계 \\n 보존)
    모두 한 덩어리 wall-of-text 를 문단으로 분리. HTML escape."""
    import html as _h
    text = (text or "").strip()
    if not text:
        return ""
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    # 문단 간격 축소(사용자 2026-06-27 '줄 간격 너무 난다'): margin 11→5, line-height
    # 1.7→1.6 — 문맥단위 가독성은 유지하되 빈공간 과다 해소.
    return "".join(
        f'<p style="margin:0 0 5px;line-height:1.6">{_h.escape(p)}</p>'
        for p in paras)


def _render_blog_page(runs: list[dict]) -> str:
    """Render blog.html — 레딧 페이지 mirror (월/일 collapse + 검색 + 카드).
    카드 = 글 제목(원문 링크) + Flash 3줄 요약 + 본문 발췌."""
    import html as _html
    import json as _json_bl
    from collections import defaultdict
    from datetime import datetime as _dt_bl, timezone as _tz_bl, timedelta as _td_bl

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get("_date", "")].append(r)

    total_runs = len(runs)
    last_ts = ""
    if runs:
        last_ts = (runs[0].get("ts") or "")[:16].replace("T", " ")
    # 블로그 관계후보 발굴 비용(kg_blog + 레거시 kg_candidate) — 단순 포워드가
    # 아니라 Gemini 추출이라 ₩0 아님(사용자 2026-06-24). DART 발굴분(kg_dart)은
    # dart_feed.html 카드로 분리 — 각 대시보드가 자기 비용만. 메인 '관계후보'는
    # 둘 합산(=블로그 카드 + DART 카드).
    _kg_today_usd, _kg_month_usd = _kg_candidate_cost_usd(
        kinds={"kg_blog", "kg_candidate"})
    _kg_cost_val = f"{_krw(_kg_today_usd)} / {_krw(_kg_month_usd)}"
    parts: list[str] = [_SCREENER_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
    · <a href="valuechain.html">🔗 밸류체인</a>
  </div>
  <h1>📝 블로그 Archive</h1>
  <p class="sub">네이버 블로그 새 글 자동 수집(30분)</p>

  <div class="stats">
    <div class="stat"><div class="stat-v">{total_runs}</div><div class="stat-l">총 수집 글</div></div>
    <div class="stat"><div class="stat-v">{_html.escape(last_ts) if last_ts else '—'}</div><div class="stat-l">마지막 수집 (KST)</div></div>
    <div class="stat"><div class="stat-v">{_kg_cost_val}</div><div class="stat-l">🔗 관계후보 발굴 비용 (오늘/이번 달)</div></div>
  </div>
{_render_kg_candidates_section(_load_kg_candidates())}
  <div class="search-bar">
    <input id="scr-search" type="text" placeholder="제목 / 요약 / 본문 검색 (예: 반도체, 수주, 전환점)" autocomplete="off" spellcheck="false">
    <button id="scr-clear" type="button" title="검색 초기화">초기화</button>
  </div>
  <p id="scr-status" class="status-line">총 {total_runs}건의 글</p>
  <div id="scr-snippets" class="snippets" style="display:none"></div>
  <div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>
""")

    if not runs:
        parts.append("""
  <div class="empty">
    아직 수집된 글이 없습니다. 블로그에 새 글이 올라오면 30분 내 자동 수집됩니다.
  </div>
</div></body></html>""")
        return "".join(parts)

    _today_kst_bl = _dt_bl.now(_tz_bl(_td_bl(hours=9))).date().isoformat()
    _this_month_bl = _today_kst_bl[:7]

    months: dict[str, list[str]] = defaultdict(list)
    for date in sorted(by_date.keys(), reverse=True):
        months[(date or "")[:7]].append(date)

    def _format_month_bl(ym: str) -> str:
        try:
            y, m = ym.split("-")
            return f"{int(y)}년 {int(m)}월"
        except Exception:
            return ym

    for month in sorted(months.keys(), reverse=True):
        month_dates = months[month]
        month_open = " open" if month == _this_month_bl else ""
        month_count = sum(len(by_date[d]) for d in month_dates)
        parts.append(
            f'<details class="month"{month_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_html.escape(_format_month_bl(month))}</span>'
            f'<span class="count">{month_count} 건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
        for date in month_dates:
            day_open = " open" if date == _today_kst_bl else ""
            day_count = len(by_date[date])
            parts.append(
                f'<details class="day"{day_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count} 건</span>'
                f'</summary>'
                f'<div class="day-body">'
            )
            for r in by_date[date]:
                raw_ts = r.get("ts") or ""
                ts_clock = raw_ts.split("T", 1)[1][:5] if "T" in raw_ts else ""
                ts_html = _html.escape(ts_clock)
                title = _html.escape(r.get("title") or "블로그 글")
                link = _html.escape(r.get("link") or "")
                # 멀티 블로그 출처(사용자 2026-06-15) — 옛 기록은 beatthemkt
                blog_src = _html.escape(r.get("blog_title") or r.get("blog_id")
                                        or "변화하는 기업을 찾아서")
                desc_raw = (r.get("desc") or "").strip()
                desc_html = _blog_desc_html(desc_raw)
                # 전문 먼저, 원문 링크는 맨 밑 (요약 미표시 — 사용자 2026-06-11)
                body_parts = []
                if desc_html:
                    body_parts.append(
                        f'<div class="analysis-b" data-section="desc">{desc_html}</div>')
                if link:
                    body_parts.append(
                        f'<div style="margin:10px 0 2px"><a href="{link}" target="_blank" rel="noopener">🔗 원문 보기</a></div>')
                plain = (title + " " + blog_src + "\n" + desc_raw)
                card_lines: list[dict] = []
                for ln in plain.splitlines():
                    s = ln.strip()
                    if len(s) >= 3:
                        card_lines.append({"sec": "brief", "txt": s[:300]})
                if len(card_lines) > 200:
                    card_lines = card_lines[:200]
                search_attr = _html.escape(plain.lower()[:6000])
                lines_attr = _html.escape(
                    _json_bl.dumps(card_lines, ensure_ascii=False))

                filename = _html.escape(r.get("_filename", ""))
                card_default_open = False   # 본문 전부 접힘 (사용자 2026-06-15, 레딧 패턴)
                card_open_attr = " open" if card_default_open else ""
                card_id = (
                    f"card-{_html.escape(r.get('_date', ''))}-{filename}"
                    .replace(".", "_")
                )

                parts.append(f"""
  <details class="card"{card_open_attr} id="{card_id}" data-date="{_html.escape(r.get('_date', ''))}" data-filename="{filename}" data-search="{search_attr}" data-lines="{lines_attr}" data-default-open="{'true' if card_default_open else 'false'}">
    <summary class="card-h">
      <span class="card-toggle">▸</span>
      <span class="domain">📝 {title} <span style="color:var(--muted);font-weight:400;font-size:12px">({ts_html} · {blog_src})</span></span>
      <button class="del-btn" type="button" title="이 글 삭제">🗑️</button>
    </summary>
    <div class="card-body">
      <div class="analysis-sec">{''.join(body_parts)}</div>
    </div>
  </details>
""")
            parts.append('</div></details>')  # close day
        parts.append('</div></details>')  # close month

    parts.append("</div>")
    parts.append(_imp_cfg("blog"))
    parts.append(_DAILY_BYTE_JS
                 .replace("api/daily_byte_delete", "api/blog_delete")
                 .replace("Daily Byte 기록을 삭제할까요?", "블로그 글을 삭제할까요?"))
    return "".join(parts)


def regenerate_blog_index() -> None:
    """Scan blog archive → write blog.html under ARCHIVE_ROOT. Called from
    bot.blog_watch after new posts + periodic refresh + startup."""
    try:
        runs = _load_blog_runs()
        html = _inject_update_banner(_render_blog_page(runs))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "blog.html").write_text(html, encoding="utf-8")
        log.info("dashboard: blog.html regenerated (%d posts)", len(runs))
    except Exception as exc:
        log.warning("dashboard: blog regen failed: %s", exc)


# ── 밸류체인(공급망) 대시보드 (valuechain.html, 사용자 2026-06-24) ───────────
# kg 관계후보(블로그·DART 계약공시 자동발굴)의 (회사)-(관계)-(대상) 엣지를 소비하는
# explorer. 회사 검색 시 공급사(→이 회사 납품)·고객/납품처(이 회사가 공급)·취급품목·
# 계열을 방향별로 묶어 보여줌 → 밸류체인 종목 발굴(고객사 호재 → 공급사 수혜 식별).
# 자동등재 무관, 전 상태(후보/승인/등재) 엣지를 탐색용으로 노출(상태·근거 함께).
_VALUECHAIN_CSS = """
<style>
.vc-chips{display:flex;flex-wrap:wrap;gap:6px}
.vc-chip{background:#1f2733;color:#c9d4e0;border:1px solid var(--border);border-radius:14px;
  padding:3px 11px;font-size:12px;cursor:pointer}
.vc-chip:hover{background:#2a3645}
.vc-focus-h{font-size:16px;font-weight:700;margin:14px 0 8px}
.vc-deg{font-size:11px;color:var(--muted);font-weight:400;margin-left:6px}
.vc-grp{margin:0 0 10px}
.vc-grp-h{font-size:12px;color:var(--muted);margin:0 0 5px}
.vc-pill{display:inline-block;background:#142033;border:1px solid #24405f;color:#cfe0f5;
  border-radius:8px;padding:2px 9px;font-size:12px;margin:0 5px 5px 0}
.vc-tip{font-size:12px;color:#8fd0a8;background:#10241a;border-radius:8px;padding:6px 10px;margin:4px 0 10px}
.vc-listh{font-size:13px;color:var(--muted);margin:12px 0 6px}
.vc-row{display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border);
  padding:7px 4px;font-size:13px}
.vc-row:hover{background:var(--surface-2,rgba(127,127,127,.05))}
/* 본문이 남는 폭을 차지 → 출처·도구(★/📝/⏰)가 우측에 붙어 클러스터(옛 space-between
   이 5개 자식을 폭 전체로 흩뿌려 아이콘이 멀리 떨어지고 빈공간 과다 — 2026-06-27). */
.vc-row>div:first-child{flex:1 1 auto;min-width:0}
.vc-rel{color:var(--muted);font-style:italic;font-size:12px}
.vc-ev{color:var(--muted);font-size:11px;margin-top:2px}
.vc-src{color:var(--muted);font-size:11px;white-space:nowrap;margin-left:auto}
/* ★/📝/⏰ 는 평소 흐리게(자리 덜 부각) → hover·활성(.on) 시 또렷. 간격도 타이트. */
.vc-row .imp-ctl{padding:0 2px;font-size:13px;opacity:.45;transition:opacity .12s}
.vc-row:hover .imp-ctl,.vc-row .imp-ctl.on{opacity:1}
/* 🗑️ 잘못된 관계 숨김 — 평소 흐리게, hover 시 또렷(imp-ctl 와 동일 결). */
.vc-del{background:none;border:none;cursor:pointer;font-size:13px;padding:0 2px;
  opacity:.35;transition:opacity .12s;line-height:1;flex:0 0 auto}
.vc-row:hover .vc-del{opacity:.85}
.vc-del:hover{opacity:1}
.vc-del:disabled{cursor:default}
.vc-tag{display:inline-block;background:#23314a;color:#9db8da;border-radius:6px;
  padding:0 6px;font-size:10px;margin-left:6px;vertical-align:middle}
/* 신선도(노후화): stale 행은 흐리게(신뢰도↓ 시각 신호), 뱃지로 사유 표기. */
.vc-row.vc-stale{opacity:.55}
.vc-fr{display:inline-block;border-radius:6px;padding:0 5px;font-size:10px}
.vc-fr-s{background:#3a3320;color:#d8c98a}
</style>
"""

_VALUECHAIN_JS = r"""
<script>
(function(){
  var raw = document.getElementById('vc-data');
  var DATA = {edges:[]};
  try { DATA = JSON.parse(raw.textContent); } catch(e){}
  // 신선도(노후화): archived 는 서버에서 제외·카운트만 전송(arch), stale 은 포함하되 dim.
  var E = DATA.edges || [];
  var ARCH_N = DATA.arch || 0;
  var STALE_N = E.filter(function(e){return e.f==='stale';}).length;
  var KG = E.filter(function(e){return e.k!=='trade';});   // 공급망·관계(블로그·DART)
  var TR = E.filter(function(e){return e.k==='trade';});    // 관세청 수출품목(회사↔품목)
  var CO = ['납품','고객','계열'];
  function norm(s){ return (s||'').toString().replace(/\s+/g,'').toLowerCase(); }
  function esc(s){ var d=document.createElement('div'); d.textContent=(s==null?'':s); return d.innerHTML; }
  function setText(id,v){ var el=document.getElementById(id); if(el) el.textContent=v; }
  var deg={}, comp={}, docs={};
  function bump(n){ if(n) deg[n]=(deg[n]||0)+1; }
  E.forEach(function(e){ bump(e.c); comp[e.c]=1;
    if(CO.indexOf(e.r)>=0){ bump(e.t); comp[e.t]=1; }
    if(e.k==='trade') comp[e.c]=1;
    if(e.s) docs[e.s]=1; });
  var entities = Object.keys(deg).sort(function(a,b){return deg[b]-deg[a];});
  // 품목(itMap)·업종(indMap) 맵 — 회사뿐 아니라 품목·업종 검색 지원(사용자 2026-06-24)
  var ITREL=['수출품목','취급품목','테마'], itMap={}, indMap={};
  E.forEach(function(e){
    if(ITREL.indexOf(e.r)>=0 && e.t) itMap[e.t]=(itMap[e.t]||0)+1;
    if(e.g) indMap[e.g]=(indMap[e.g]||0)+1;
  });
  function _pick(map,nq,exactOnly){
    var ks=Object.keys(map);
    var ex=ks.filter(function(n){return norm(n)===nq;});
    if(ex.length) return ex.sort(function(a,b){return map[b]-map[a];})[0];
    if(exactOnly) return null;
    var pa=ks.filter(function(n){return norm(n).indexOf(nq)>=0;});
    return pa.length? pa.sort(function(a,b){return map[b]-map[a];})[0] : null;
  }
  function resolveKind(nq){
    // 정확 매칭 우선(회사>품목>업종) → 부분 매칭(회사>품목>업종). 모듈 resolve_kind 와 동조.
    var r;
    if((r=_pick(deg,nq,true))) return ['company',r];
    if((r=_pick(itMap,nq,true))) return ['item',r];
    if((r=_pick(indMap,nq,true))) return ['industry',r];
    if((r=_pick(deg,nq,false))) return ['company',r];
    if((r=_pick(itMap,nq,false))) return ['item',r];
    if((r=_pick(indMap,nq,false))) return ['industry',r];
    return [null,null];
  }
  setText('vc-stat-edges', E.length);
  setText('vc-stat-edges-l', '관계(엣지) · 공급망 '+KG.length+' · 관세청 '+TR.length+
    (STALE_N?' · 🟡오래됨 '+STALE_N:'')+(ARCH_N?' · ⚪보관 '+ARCH_N+'(숨김)':''));
  setText('vc-stat-nodes', Object.keys(comp).length);
  setText('vc-stat-docs', Object.keys(docs).length);
  var chipWrap = document.getElementById('vc-chips');
  entities.slice(0,28).forEach(function(name){
    var b=document.createElement('button'); b.className='vc-chip';
    b.textContent=name+' '+deg[name];
    b.onclick=function(){ var s=document.getElementById('vc-search'); s.value=name; render(name); };
    chipWrap.appendChild(b);
  });
  function attrEsc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
  function freshBadge(f){
    if(f==='stale') return ' · <span class="vc-fr vc-fr-s" title="학습 후 오래 재확인 안 됨 — 신뢰도↓. 분석 컨텍스트에선 제외됨">🟡 오래됨</span>';
    return '';
  }
  function edgeRow(e){
    var tag = e.k==='trade' ? '<span class="vc-tag">관세청</span>' : '';
    var iid = attrEsc(e.c+'|'+e.r+'|'+e.t);   // 속성 안전(따옴표 포함 회사명 대응)
    var fc = (e.f==='stale') ? ' vc-stale' : '';   // 노후 관계 dim
    return '<div class="vc-row'+fc+'" data-imp-id="'+iid+'"><div><b>'+esc(e.c)+'</b> <span class="vc-rel">'+esc(e.r)+'</span> → '+esc(e.t)+tag+
      (e.e?'<div class="vc-ev">'+esc(e.e)+'</div>':'')+'</div>'+
      '<div class="vc-src">'+esc((e.s||'').replace(/^blog:|^dart:/,''))+(e.st?' · '+esc(e.st):'')+(e.d?' · 📅'+esc(e.d)+' 학습':'')+freshBadge(e.f)+'</div>'+
      '<button class="vc-del" type="button" title="이 관계 숨기기 (잘못된 매칭 제거)">🗑️</button></div>';
  }
  // 🗑️ 잘못된 관계 숨김 — 자동 도출 엣지라 영구 suppression(서버) + 클라 배열·DOM 제거.
  function dropEdge(id){
    [E,KG,TR].forEach(function(arr){
      for(var i=arr.length-1;i>=0;i--){ var x=arr[i];
        if((x.c+'|'+x.r+'|'+x.t)===id) arr.splice(i,1); } });
  }
  document.addEventListener('click', function(ev){
    var btn=ev.target.closest && ev.target.closest('.vc-del');
    if(!btn) return;
    var row=btn.closest('.vc-row'); if(!row) return;
    var id=row.getAttribute('data-imp-id'); if(!id) return;
    if(!confirm('이 관계를 숨길까요?\n잘못된 매칭 제거 — 다음부터 표시되지 않습니다.')) return;
    btn.disabled=true; btn.textContent='⏳';
    fetch('api/vc_suppress',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:id})}).then(function(r){return r.json();})
      .then(function(j){ if(j&&j.ok){ dropEdge(id); row.remove(); }
        else { btn.disabled=false; btn.textContent='🗑️'; alert('숨김 실패'); } })
      .catch(function(){ btn.disabled=false; btn.textContent='🗑️'; alert('숨김 실패(네트워크)'); });
  });
  function grp(title, items, fmt){
    if(!items.length) return '';
    return '<div class="vc-grp"><div class="vc-grp-h">'+title+' ('+items.length+')</div>'+items.map(fmt).join('')+'</div>';
  }
  function pillC(e){ return '<span class="vc-pill">'+esc(e.c)+'</span>'; }
  function pillT(e){ return '<span class="vc-pill">'+esc(e.t)+'</span>'; }
  function render(q){
    var focus=document.getElementById('vc-focus'), list=document.getElementById('vc-list');
    var nq=norm(q);
    if(!nq){
      focus.innerHTML='';
      list.innerHTML='<div class="vc-listh">공급망·관계 '+KG.length+'건(블로그·DART)'+
        (KG.length>400?' · 상위 400':'')+' — 관세청 수출품목 '+TR.length+'건은 회사 검색 시 표시</div>'+
        KG.slice(0,400).map(edgeRow).join('');
      if(window.__impApply) window.__impApply();
      return;
    }
    var rk=resolveKind(nq), KIND=rk[0], NAME=rk[1];
    if(KIND==='company'){
      var X=NAME, nx=norm(X);
      var suppliers=KG.filter(function(e){return norm(e.t)===nx && ['납품','고객'].indexOf(e.r)>=0;});
      var customers=KG.filter(function(e){return norm(e.c)===nx && ['납품','고객'].indexOf(e.r)>=0;});
      var products =KG.filter(function(e){return norm(e.c)===nx && e.r==='취급품목';});
      var themes   =KG.filter(function(e){return norm(e.c)===nx && e.r==='테마';});
      var affil    =KG.filter(function(e){return (norm(e.c)===nx||norm(e.t)===nx) && e.r==='계열';});
      var exports  =TR.filter(function(e){return norm(e.c)===nx;});
      // 동종 회사(peer) = 같은 수출품목을 다루는 다른 회사(관세청)
      var myItems={}; exports.forEach(function(e){ myItems[norm(e.t)]=1; });
      var peerCnt={};
      TR.forEach(function(e){ if(norm(e.c)!==nx && myItems[norm(e.t)]) peerCnt[e.c]=(peerCnt[e.c]||0)+1; });
      var peers=Object.keys(peerCnt).sort(function(a,b){return peerCnt[b]-peerCnt[a];}).slice(0,40);
      focus.innerHTML='<div class="vc-focus-h">🏢 '+esc(X)+'<span class="vc-deg">연결 '+(deg[X]||0)+'</span></div>'+
        grp('⬅️ 공급사 (이 회사에 납품)', suppliers, pillC)+
        grp('➡️ 고객·납품처 (이 회사가 공급)', customers, pillT)+
        grp('📦 취급품목 (계약공시)', products, pillT)+
        grp('🛃 수출품목 (관세청)', exports, pillT)+
        (peers.length?'<div class="vc-grp"><div class="vc-grp-h">👥 동종 회사 (같은 수출품목, '+peers.length+')</div>'+
          peers.map(function(n){return '<span class="vc-pill">'+esc(n)+' '+peerCnt[n]+'</span>';}).join('')+'</div>':'')+
        grp('🏷️ 테마', themes, pillT)+
        grp('🔗 계열', affil, function(e){return '<span class="vc-pill">'+esc(norm(e.c)===nx?e.t:e.c)+'</span>';})+
        (suppliers.length?'<div class="vc-tip">💡 '+esc(X)+' 호재·실적 시 위 공급사들이 수혜 후보</div>':'');
    } else if(KIND==='item'){
      var IT=NAME, nit=norm(IT);
      var customs=E.filter(function(e){return e.r==='수출품목'&&norm(e.t)===nit;});
      var contract=E.filter(function(e){return (e.r==='취급품목'||e.r==='테마')&&norm(e.t)===nit;});
      var hs='',inds={}; customs.forEach(function(e){ if(e.e&&!hs)hs=e.e; if(e.g)inds[e.g]=1; });
      focus.innerHTML='<div class="vc-focus-h">📦 '+esc(IT)+'<span class="vc-deg">품목'+(hs?' · '+esc(hs):'')+'</span></div>'+
        grp('🛃 수출 회사 (관세청)', customs, pillC)+
        grp('📑 취급 회사 (계약공시)', contract, pillC)+
        (Object.keys(inds).length?'<div class="vc-grp"><div class="vc-grp-h">🏭 업종</div>'+
          Object.keys(inds).map(function(g){return '<span class="vc-pill">'+esc(g)+'</span>';}).join('')+'</div>':'')+
        '<div class="vc-tip">💡 같은 품목 회사 = 동종/피어 — 한 종목 호재 시 함께 점검</div>';
    } else if(KIND==='industry'){
      var nind=norm(NAME);
      var sc={},cos=[]; E.forEach(function(e){ if(norm(e.g)===nind&&e.c&&!sc[norm(e.c)]){sc[norm(e.c)]=1;cos.push(e.c);} });
      focus.innerHTML='<div class="vc-focus-h">🏭 '+esc(NAME)+'<span class="vc-deg">업종 · 회사 '+cos.length+'</span></div>'+
        '<div class="vc-grp"><div class="vc-grp-h">회사 ('+cos.length+')</div>'+
        cos.slice(0,80).map(function(n){return '<span class="vc-pill">'+esc(n)+'</span>';}).join('')+'</div>';
    } else { focus.innerHTML=''; }
    var f=E.filter(function(e){return norm(e.c).indexOf(nq)>=0 || norm(e.t).indexOf(nq)>=0;});
    list.innerHTML='<div class="vc-listh">관계 '+f.length+'건 — "'+esc(q)+'"</div>'+f.slice(0,400).map(edgeRow).join('');
    if(window.__impApply) window.__impApply();
  }
  var s=document.getElementById('vc-search');
  s.addEventListener('input', function(){ render(s.value); });
  document.getElementById('vc-clear').addEventListener('click', function(){ s.value=''; render(''); });
  render('');
})();
</script>
"""


def _render_valuechain_page(edges: list[dict], cost_today: float = 0.0,
                            cost_month: float = 0.0) -> str:
    """valuechain.html — kg 엣지 explorer(공급망/밸류체인). 회사 검색 시 공급사·
    고객·취급품목·계열을 방향별로. 데이터는 <script type=application/json> 임베드 +
    클라이언트 JS 검색/필터(서버 무상태)."""
    import html as _h
    import json as _j
    # archived(노후 보관)는 클라에 싣지 않고 카운트만 — MVP 는 재확인 부활(Phase 2)이
    # 없어 1년+ 미승인 엣지가 다수 archived 될 수 있어 payload 비대화 방지. active/stale
    # 만 전송(stale 은 dim 표시). 카운트는 헤더 라벨로 가시화(삭제 아님 — 실수 #11).
    _ok = [e for e in edges if e.get("company") and e.get("target")]
    arch_n = sum(1 for e in _ok if e.get("freshness") == "archived")
    payload = {"arch": arch_n, "edges": [{
        "c": e.get("company", ""), "r": e.get("relation", ""),
        "t": e.get("target", ""), "e": (e.get("evidence", "") or "")[:160],
        "s": e.get("source", ""), "st": e.get("status", ""),
        "k": e.get("kind", "kg"), "g": e.get("industry", ""),
        "d": (e.get("date", "") or "")[:10],   # 학습(추출)일 — kg 만 보유
        "f": e.get("freshness", "active"),      # 신선도 active|stale
    } for e in _ok if e.get("freshness") != "archived"]}
    # </script> 차단 + JSON 안전 임베드
    data_json = _j.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    parts = [_SCREENER_CSS, _VALUECHAIN_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="market.html">🌍 홈</a> · <a href="index.html">🦉 종목분석</a>
    · <a href="dart_feed.html">📋 DART 공시</a> · <a href="blog.html">📝 블로그</a>
  </div>
  <h1>🔗 밸류체인</h1>
  <p class="sub">블로그·DART 계약공시(kg) + 관세청 수출입 레퍼런스북을 집합한 통합 그래프 · 회사 검색 시 공급사·고객·수출품목·동종 회사 연결(고객사 호재 → 공급사 수혜 발굴) · 모든 소스 갱신 자동 반영</p>
  <details class="month" style="margin:4px 0 12px">
    <summary class="month-head" style="cursor:pointer"><span>ℹ️ 사용법 — 처음이면 펼쳐 보세요</span><span class="count">가이드</span></summary>
    <div class="month-body" style="padding:10px 14px;font-size:13px;line-height:1.75;color:var(--fg)">
      <b>1) 검색</b> — <b>회사·품목·업종</b> 아무거나 검색됩니다(수출입 대시보드처럼). 예) <code>SK하이닉스</code>(회사)→공급사·고객·수출품목 / <code>메모리반도체</code>(품목)→그 품목 수출·취급 회사 / <code>반도체</code>(업종)→업종 회사. <b>주요 회사 칩</b> 클릭 = 빠른 검색. 텔레그램도 <code>/valuechain 메모리반도체</code> 동일.<br>
      <b>2) 그룹 의미</b><br>
      &nbsp;&nbsp;⬅️ <b>공급사</b> — 이 회사에 <b>납품</b>하는 회사들 (이 회사가 잘되면 <b>수혜 후보</b>)<br>
      &nbsp;&nbsp;➡️ <b>고객·납품처</b> — 이 회사가 <b>공급</b>하는 상대<br>
      &nbsp;&nbsp;🛃 <b>수출품목(관세청)</b> · 📦 <b>취급품목(계약공시)</b> — 이 회사가 다루는 품목<br>
      &nbsp;&nbsp;👥 <b>동종 회사</b> — 같은 수출품목을 다루는 경쟁/피어 · 🏷️ 테마 · 🔗 계열<br>
      <b>3) 데이터 출처</b> — <b>공급망</b> 태그 = 블로그·DART 계약공시 자동발굴(kg) / <b>관세청</b> 태그 = 수출입 레퍼런스북(MTI·DART 매출구성·테마·운영자 보강 병합). 각 소스가 갱신되면 자동 반영됩니다.<br>
      <b>4) 활용</b> — 대형 고객사(예: SK하이닉스)의 호재·실적 → <b>그 공급사들이 수혜 후보</b>. 동종 회사로 peer 비교, 취급/수출품목으로 사업 파악.<br>
      <b>5) 정리</b> — "관계 N건" 목록의 각 행 끝 <b>🗑️</b> = 잘못된 매칭 숨김(영구). 이후 페이지·텔레그램·분석 컨텍스트 모두에서 제외됩니다.<br>
      <b>6) 신선도</b> — 자동발굴(kg) 관계는 학습 후 오래(기본 6개월) 재확인이 안 되면 <b>🟡 오래됨</b>으로 흐려지고 <b>분석 컨텍스트에서 제외</b>됩니다. 1년 넘으면 <b>⚪ 보관</b>으로 목록에서 숨겨집니다(삭제 아님 — 상단 카운트로만 표기). 운영자가 <a href="blog.html">관계후보에서 승인</a>한 관계와 관세청 데이터는 노후화 면제(항상 표시).<br>
      <b>⚠️ 주의</b> — 자동발굴(LLM·관세청)이라 일부 부정확할 수 있습니다. <b>참고 신호</b>로만 쓰고 확정 사실로 단정하지 마세요. 후보 검토·반영은 <a href="blog.html">📝 블로그 → 🔗 관계후보</a>에서.
    </div>
  </details>
  <div class="stats">
    <div class="stat"><div class="stat-v" id="vc-stat-edges">—</div><div class="stat-l" id="vc-stat-edges-l">관계(엣지)</div></div>
    <div class="stat"><div class="stat-v" id="vc-stat-nodes">—</div><div class="stat-l">회사(노드)</div></div>
    <div class="stat"><div class="stat-v" id="vc-stat-docs">—</div><div class="stat-l">출처 문서</div></div>
    <div class="stat"><div class="stat-v">{_krw(cost_today)} / {_krw(cost_month)}</div><div class="stat-l">KG 발굴 비용 (오늘/이번 달) · 관세청 무료</div></div>
  </div>
  <div style="margin:12px 0 6px;color:var(--muted);font-size:13px">주요 회사 (연결수) — 클릭하면 필터</div>
  <div id="vc-chips" class="vc-chips"></div>
  <div class="search-bar" style="margin-top:12px">
    <input id="vc-search" type="text" placeholder="회사·품목·업종 검색 (예: SK하이닉스, 메모리반도체, 반도체)" autocomplete="off" spellcheck="false">
    <button id="vc-clear" type="button">초기화</button>
  </div>
  <div id="vc-focus"></div>
  <div id="vc-list"></div>
</div>
<script id="vc-data" type="application/json">{data_json}</script>
""")
    # 중요 블록을 _VALUECHAIN_JS 앞에 — render('') 의 window.__impApply() 가
    # 첫 렌더에서도 정의돼 있도록(로드순서, 코드리뷰 2026-06-26).
    parts.append(_imp_cfg("valuechain", card_sel=".vc-row", head_sel="",
                          search_sel="#vc-search"))
    parts.append(_IMPORTANT_BLOCK)
    parts.append(_VALUECHAIN_JS)
    parts.append("</body></html>")
    return "".join(parts)


def _load_valuechain_edges() -> list[dict]:
    """밸류체인 그래프 엣지 = 다소스 집합. bot.valuechain.load_edges 단일 소스
    재사용(②NOAH 주입·③/valuechain 명령과 동일 그래프 — drift 방지). graceful([])."""
    try:
        from bot import valuechain
        # archived 포함 — 페이지가 신선도 카운트 표시 + 클라가 목록에서 숨김/dim 처리
        # (서버는 전수 제공, 표시 정책은 클라). NOAH/텔레그램은 기본 load_edges(제외).
        return valuechain.load_edges(include_archived=True)
    except Exception as exc:
        log.warning("valuechain: edges load failed: %s", exc)
        return []


def regenerate_valuechain_index() -> None:
    """다소스 집합(_load_valuechain_edges) → write valuechain.html. blog_watch/
    dart_feed 추출 후 + approve + 주기 regen 에서 호출 → 모든 소스 갱신 반영. graceful."""
    try:
        edges = _load_valuechain_edges()
        ct, cm = _kg_candidate_cost_usd()
        html = _inject_update_banner(_render_valuechain_page(edges, ct, cm))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "valuechain.html").write_text(html, encoding="utf-8")
        log.info("dashboard: valuechain.html regenerated (%d edges)", len(edges))
    except Exception as exc:
        log.warning("dashboard: valuechain regen failed: %s", exc)


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
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
    · <a href="screener.html">📊 Screener</a>
    · <a href="daily_byte.html">📰 Daily Byte</a>
  </div>
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
    """Watchlist merged into paper page — redirect."""
    regenerate_paper_index()


# ── 페이퍼 트레이딩 대시보드 (E0, 2026-06-05) — paper.html ──────────────────
# bot/paper_trading.py 의 모의 계좌(포지션·P&L)를 읽어 렌더. 명령(/paper*) 시
# + startup + 자정 regen. 실거래 실행 골격의 read-only surface (docs/execution_
# architecture.md E0). _SCREENER_CSS 페이지-opener + _PF_CSS 재사용.
def _paper_nav(active: str = "paper") -> str:
    def _a(href, label, key):
        return f"<b>{label}</b>" if key == active else f'<a href="{href}">{label}</a>'
    return (
        '<div class="nav">'
        '<a href="market.html">🌍 홈</a>'
        ' · <a href="index.html">🦉 종목분석</a>'
        ' · <a href="screener.html">📊 Screener</a>'
        ' · ' + _a("paper.html", "🔔 워치리스트", "paper")
        + '</div>')


def _render_paper_page(summ: dict, watches: list[dict] | None = None, alerts: list[dict] | None = None) -> str:
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

    # ── Watchlist section (merged into paper page) — 항상 렌더 ──────────────
    # 페이퍼 계좌가 비어도(거래 0) 워치 목록은 보여야 한다 — 빈-계좌 early
    # return 경로에도 포함(사용자 2026-06-09: 워치한 종목이 사라져 보임 = 이
    # early-return 이 워치 섹션을 건너뛴 것이 원인). 워치 0개여도 빈 상태 명시.
    _wl = watches or []
    _al = alerts or []
    import html as _whtml

    def _wesc(s):
        return _whtml.escape(str(s))
    _wr = ""
    for w in _wl:
        _conds = " ".join(w.get("conditions") or [])
        _added = (w.get("added") or "")[:16].replace("T", " ")
        _wr += (
            f"<tr><td><b>{_wesc(w.get('ticker',''))}</b></td>"
            f"<td><code>{_wesc(_conds)}</code></td>"
            f"<td class='muted'>{_wesc(_added)}</td>"
            f"<td class='muted'>{_wesc(w.get('id',''))}</td></tr>"
        )
    if not _wr:
        _wr = "<tr><td colspan='4' class='muted'>감시 중인 종목 없음 — 텔레그램에서 <code>/watch TICKER 조건</code></td></tr>"
    _ar = ""
    for a in _al[:200]:
        _ats = (a.get("ts") or "")[:16].replace("T", " ")
        _ahits = " · ".join(a.get("hits") or [])
        _ar += (
            f"<tr><td class='muted'>{_wesc(_ats)}</td>"
            f"<td><b>{_wesc(a.get('ticker',''))}</b></td>"
            f"<td>{_wesc(_ahits)}</td></tr>"
        )
    if not _ar:
        _ar = "<tr><td colspan='3' class='muted'>아직 발생한 알림 없음</td></tr>"
    wl_section = (
        '<hr style="border:none;border-top:1px solid var(--border);margin:32px 0 24px">'
        f'<h2>📋 활성 워치 ({len(_wl)})</h2>'
        '<table class="pf-tbl"><thead><tr><th>종목</th><th>조건</th><th>등록</th><th>id</th></tr></thead>'
        f'<tbody>{_wr}</tbody></table>'
        f'<h2>🔔 알림 이력 ({len(_al)})</h2>'
        '<table class="pf-tbl"><thead><tr><th>시각</th><th>종목</th><th>충족 조건</th></tr></thead>'
        f'<tbody>{_ar}</tbody></table>'
        '<p class="sub">조건: <code>rsi&lt;30 rsi&gt;70 price&gt;X price&lt;X &gt;sma50 &lt;sma200 52whigh 52wlow earnings foreignbuy foreignsell instbuy instsell</code></p>'
    )

    if not rows and not trades:
        # 페이퍼 계좌는 비었지만 워치리스트는 항상 표시.
        return _SCREENER_CSS + _PF_CSS + (
            '<div class="wrap">' + nav + '<h1>🔔 워치리스트</h1>'
            '<p class="sub">조건 알림(30분 체크, ₩0) + 페이퍼 트레이딩(모의 매매, 리스크 0)</p>'
            + halt_banner + e1_banner
            + '<p class="sub">아직 모의 거래가 없습니다. 텔레그램에서 '
            '<b>/paper buy TICKER 수량</b> 으로 시작하세요. (모의·돈 0)</p>'
            + gate_line + wl_section + '</div>')

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

    # wl_section 은 위(빈-계좌 early-return 공유)에서 이미 빌드됨.
    return (_SCREENER_CSS + _PF_CSS + '<div class="wrap">' + nav
            + '<h1>🔔 워치리스트</h1>'
            '<p class="sub">조건 알림(30분 체크, ₩0) + 페이퍼 트레이딩(모의 매매, 리스크 0)</p>'
            + halt_banner + e1_banner + stats + stats_extra + curve_html + pos_block + pend_block
            + tr_block + audit_block + note + gate_line + wl_section + "</div>")


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

            from bot.stock_screener import fmt_metric_value as _fmv
            extra_vals = []
            for k, v in h.items():
                if k in ("code", "ticker", "name", "market", "mcap", "price"):
                    continue
                if v is not None:
                    extra_vals.append(f"{k}:{_fmv(v)}" if isinstance(v, (int, float)) else f"{k}:{v}")
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
  <p class="sub">정량 조건으로 KR + US + JP + HK + CN + TW 종목 필터 (pykrx/yfinance, ₩0).
  텔레그램: <code>/screen PER&lt;15 PBR&lt;1</code> · <code>/screen us PER&lt;15</code> · <code>/screen jp minervini</code> · <code>/screen valueup</code> · <code>/screen list</code></p>
  <div id="screen-impbar" style="margin:10px 0"></div>
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
{_imp_cfg('screen', card_sel='details[data-date]', head_sel='summary', id_attrs=['date', 'file'], filter_into='#screen-impbar')}
{_IMPORTANT_BLOCK}
</body>
</html>
""".replace("{cards}", cards)


def regenerate_screen_index() -> None:
    """Screen merged into screener page — redirect."""
    regenerate_screener_index()


def regenerate_paper_index() -> None:
    """페이퍼 계좌 + 워치리스트 → paper.html. 명령/startup/자정 regen 에서 호출. 에러 무해."""
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
        # Load watchlist data for merged page
        watches: list[dict] = []
        alerts: list[dict] = []
        try:
            from bot.watchlist import all_watches, load_alerts
            watches = all_watches()
            alerts = load_alerts(200)
        except Exception:
            pass
        html = _render_paper_page(summ, watches=watches, alerts=alerts)
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "paper.html").write_text(html, encoding="utf-8")
        log.info("dashboard: paper.html regenerated (%d positions, %d watches)",
                 summ.get("n_positions", 0), len(watches))
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
    row(['자산 스냅샷', snapDate()]);row([]);
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
    if(hasChg)h.push('손익변동');h.push('판정');row(h);
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
        '<a href="market.html">🌍 홈</a>'
        ' · <a href="budget.html">📒 가계부</a>'
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

    _free_cash = _alloc.get("자유입출금 자산") or 0
    cash_stat = (f'<div class="stat"><div class="stat-num">{_pf_won(_free_cash)}</div>'
                  '<div class="stat-lbl">자유입출금</div></div>') if _free_cash else ""
    stats = (
        '<div class="stats">'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("순자산"))}</div><div class="stat-lbl">순자산</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("총자산"))}</div><div class="stat-lbl">총자산</div></div>'
        f'<div class="stat"><div class="stat-num">{_pf_won(nw.get("총부채"))}</div><div class="stat-lbl">총부채</div></div>'
        + cash_stat +
        f'<div class="stat"><div class="stat-num">{_pf_won(eval_sum)}</div><div class="stat-lbl">주식 평가</div></div>'
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
            '<label><input type="checkbox" id="pf-noah"> 분석만</label>'
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
             '<th data-k="noah" data-t="s">판정</th></tr></thead>')
    # 헤더 카운트 = 고유 종목 수(증권사 중복 제외, 위에서 렌더 시점 재계산).
    # 행은 증권사별 포지션을 유지하므로(증권사 필터 보존) 건수와 다르면 병기.
    _hdr_cnt = (f'{_distinct}종목 · {_positions}건'
                if _positions != _distinct else f'{_distinct}종목')
    holdings_block = ('<div class="pf-card"><div class="pf-h">보유 종목 (' + _hdr_cnt
                      + (f' · 분석 {noah_n}' if noah_n else '') + ')</div>' + _ctl
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
            '표 헤더 클릭 정렬 · 검색/증권사/분석 필터 가능. '
            '티커 매칭 종목은 종목명 클릭 시 분석으로 연결(분석 기록 있을 때). '
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
    row(['가계부 스냅샷', d]);row([]);
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
    """가계부 페이지 nav — 홈 + 자산 (Group 1 peers)."""
    return (
        '<div class="nav">'
        '<a href="market.html">🌍 홈</a>'
        ' · <a href="portfolio.html">💼 자산</a>'
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
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
    · <a href="screener.html">📊 Screener</a>
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


# ═════════════════════════════════════════════════════════════════════
# DART 공시 피드 대시보드 (dart_feed.html)
# ═════════════════════════════════════════════════════════════════════


def _load_dart_feed_data(days_back: int = 30) -> dict[str, list[dict]]:
    try:
        from bot.dart_feed import load_all_archives
        return load_all_archives(days_back)
    except Exception:
        return {}


# 칩 순서 = 사용자 지정 고정 (2026-06-12): 전체·중요·미파싱(플래그) 다음
# 실적 → 계약 → 신규시설투자 → 주주환원 → 자금조달 → 배당(신설 분리) →
# 지분공시 → 리스크 → 소송 → 회사구조 → 자산양수도 → 조회공시 → IR.
_DART_CATEGORIES = ["전체", "실적", "계약", "신규시설투자", "주주환원",
                    "자금조달", "지분공시", "배당", "리스크", "소송",
                    "회사구조", "자산양수도", "조회공시", "IR"]
# 배당↔지분공시 위치 교환 (사용자 2026-06-12 2차)

_DART_CAT_COLORS = {
    "계약": "#26a69a", "실적": "#42a5f5", "IR": "#5c6bc0", "주주환원": "#ab47bc",
    "자금조달": "#f5a623", "신규시설투자": "#2196f3", "자산양수도": "#009688",
    "리스크": "#ef5350", "소송": "#26a69a", "회사구조": "#ff7043",
    "조회공시": "#8d6e63",
    "지분공시": "#ec407a",
}

_WEEKDAY_KR = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}


# DART 공시 페이지 전용 CSS — 반드시 컨텐츠(카드 ~1000+개) **앞**에 emit.
# 카드 뒤에 두면 첫 paint 가 무스타일 + 접힘(.collapsed display:none) 미적용
# 상태로 전체 펼쳐져 보였다가 늦게 도착한 CSS 가 파싱되며 스냅 — '처음에
# 깨졌다 복귀 아주 잠깐'(사용자 2026-06-10)의 원인. #179 의 _THEME_JS 선행
# 이동과 같은 클래스 버그(이번엔 CSS).
_DART_FEED_CSS = """
<style>
.df-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:16px}
.df-pills{display:flex;flex-wrap:wrap;gap:6px}
.df-pill{padding:4px 12px;border-radius:16px;border:1px solid var(--border,#333);background:transparent;color:var(--text,#1f2937);cursor:pointer;font-size:13px;white-space:nowrap}
.df-pill.active{background:var(--accent,#ef5350);color:#fff;border-color:var(--accent,#ef5350)}
.df-right{display:flex;gap:8px;align-items:center}
.df-view-btns{display:flex;gap:2px;background:var(--card,#1a1f2b);border-radius:6px;padding:2px}
.df-csv-btn{padding:6px 12px;border-radius:6px;border:1px solid var(--border,#333);background:var(--card,#1a1f2b);color:var(--text,#1f2937);cursor:pointer;font-size:13px;white-space:nowrap}
.df-csv-btn:hover{background:var(--accent,#3b82f6);color:#fff;border-color:var(--accent,#3b82f6)}
.df-vbtn{padding:6px 8px;background:transparent;border:none;color:var(--muted,#888);cursor:pointer;border-radius:4px;display:flex;align-items:center}
.df-vbtn.active{background:var(--accent,#3b82f6);color:#fff}
.df-month{margin-bottom:20px}
.df-month-hd{display:flex;align-items:center;gap:9px;cursor:pointer;user-select:none;font-size:18px;font-weight:700;padding:8px 0;border-bottom:2px solid var(--border,#333);margin-bottom:12px}
.df-month-lbl{flex:0 0 auto}
.df-month-cnt{margin-left:auto;font-size:12px;font-weight:400;color:var(--muted,#888);background:var(--card,#1a1f2b);padding:2px 10px;border-radius:10px}
.df-month.collapsed .df-month-body{display:none}
.df-month-body{padding-left:2px}
.df-date-group{margin-bottom:18px}
.df-date-hd{display:flex;gap:8px;align-items:baseline;cursor:pointer;user-select:none;padding:6px 0;border-bottom:1px solid var(--border,#333);margin-bottom:12px;font-weight:600;font-size:15px}
.df-date-group.collapsed .df-date-body{display:none}
.df-caret{font-size:11px;color:var(--muted,#888);display:inline-block;transition:transform .15s;flex:0 0 auto}
.collapsed>.df-month-hd>.df-caret,.collapsed>.df-date-hd>.df-caret{transform:rotate(-90deg)}
.df-searching .df-month-body,.df-searching .df-date-body{display:block!important}
.df-date-meta{font-size:12px;color:var(--muted,#888);font-weight:400;margin-left:auto}
.df-date-label{margin-left:8px}
.df-date-cnt{margin-left:8px;font-weight:600;color:var(--text,#1f2937)}
.df-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.df-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.df-grid{grid-template-columns:1fr}}
.df-card{background:var(--card,#1a1f2b);border:1px solid var(--border,#2a2f3a);border-radius:8px;padding:14px;font-size:13px;display:flex;flex-direction:column;gap:6px}
.df-card.hidden{display:none}
/* 중요(🔥 금색)/미파싱(⚠️ 파랑 점선) 색상 구별 — 두 색 명확 분리
   (사용자 2026-06-12 '중요랑 미파싱은 색깔을 다르게'; 옛 주황은 금색과
   혼동) */
.df-card.df-significant{border-color:#d4a017;box-shadow:0 0 0 1px #d4a01755}
.df-card.df-unparsed{border-style:dashed;border-color:#5c9ce6aa}
/* 미파싱제외(의도) — 회색 점선, 진짜 미파싱(파랑 점선)과 구별(사용자 2026-06-14) */
.df-card.df-noparse{border-style:dotted;border-color:#6b727e88}
.df-badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;align-self:flex-start;line-height:1.5}
.df-badge-sig{background:#d4a01726;color:#b8860b;border:1px solid #d4a01766}
.df-badge-unp{background:#5c9ce618;color:#3b82c4;border:1px solid #5c9ce655}
.df-badge-noparse{background:#6b727e18;color:#8b919b;border:1px solid #6b727e55}
.df-pill-sig.active{background:#d4a017;border-color:#d4a017}
.df-pill-unp.active{background:#5c9ce6;border-color:#5c9ce6}
.df-pill-noparse.active{background:#6b727e;border-color:#6b727e}
.df-card-hd{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.df-corp{font-weight:700;font-size:15px;color:var(--text,#1f2937);text-decoration:none}
.df-corp:hover{text-decoration:underline}
.df-ticker-link{font-size:11px;color:var(--muted,#888);text-decoration:none}
.df-ticker-link:hover{color:var(--accent,#3b82f6)}
.df-meta{display:flex;align-items:center;gap:6px;flex-shrink:0}
.df-dt{font-size:12px;color:var(--muted,#888)}
.df-cat{font-size:11px;padding:2px 8px;border-radius:4px;color:#fff;white-space:nowrap}
.df-report{color:var(--muted,#6b7280);font-size:12px;line-height:1.4}
.df-detail-ln{color:var(--text,#1f2937);font-size:12px;line-height:1.5}
/* list view */
.df-grid.list-view{display:flex;flex-direction:column;gap:4px}
.df-grid.list-view .df-card{flex-direction:row;align-items:center;gap:12px;padding:8px 14px}
.df-grid.list-view .df-card-hd{flex-direction:row;align-items:center;min-width:160px}
.df-grid.list-view .df-report{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.df-grid.list-view .df-detail-ln{display:none}
.df-grid.list-view .df-detail-ln.df-mcap{display:block;margin-left:auto;flex-shrink:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:11px;color:var(--muted,#888)}
.df-grid.list-view .df-meta{order:-1;min-width:80px}
</style>
"""


def _render_dart_feed_page(by_date: dict[str, list[dict]]) -> str:
    import html as _html
    from datetime import datetime as _dt

    total = sum(len(v) for v in by_date.values())

    # 백필 v4 상태 라벨은 제거 (사용자 2026-06-13 '이제 필요없잖아' —
    # 일회성 이벤트 완료, backfill_v4_status() 는 CLI 진단용으로 보존).

    # 시총·현재가 렌더 부착(배치 #11) — FSC 12h 디스크 캐시 + 렌더당 코드
    # 1회 메모 + cold-fetch 시간예산 45s(30일 윈도 수백 코드 첫 채움이 1분
    # 사이클을 막지 않게 — 최신 카드부터 채워지고 다음 regen 이 이어감).
    import time as _time
    _mc_memo: dict[str, list] = {}
    _mc_deadline = _time.time() + 45.0

    def _mc_lines(code: str) -> list:
        if code in _mc_memo:
            return _mc_memo[code]
        if _time.time() > _mc_deadline:
            return []
        try:
            from bot.dart_feed import _market_cap_price_lines
            out = _market_cap_price_lines(code)
        except Exception:
            out = []
        _mc_memo[code] = out
        return out

    def _equity_noise(it: dict) -> bool:
        """#18 지분공시 노이즈컷 — True 이면 숨김.

        2026-06-12 '지분공시도 다 파싱': 임원·주요주주 소유상황은 elestock
        파싱된(detail 보유) 카드만 노출 — 파싱 전/실패는 기존대로 숨겨
        제목만 카드 홍수 방지. 대량보유는 기존 정책(±5%p 미만 변동 컷).
        기타경영사항(자율공시)도 동일 — 소송성 파싱(detail)된 것만 노출
        (사용자 2026-06-12 소송 5예시, 비소송 자율공시 PR 류 차단)."""
        rn = it.get("report_nm", "")
        if "기타경영사항" in rn or "투자판단" in rn:
            return not (it.get("detail") or [])
        if it.get("category") != "지분공시":
            return False
        if "대량보유" not in rn:
            # 소유상황(elestock) + 최대주주등소유주식변동신고서(공정거래법,
            # 라이브 2026-06-13) = 고빈도(36/3일) → detail 파싱된 것만 노출,
            # 미파싱은 숨겨 제목 홍수 방지 (대량보유와 같은 정책).
            if ("소유상황" in rn or "소유주식변동" in rn
                    or ("주식보유" in rn and "변동" in rn)):
                return not (it.get("detail") or [])
            return True
        det = it.get("detail") or []
        if not det:
            return False
        for dl in det:
            s = str(dl)
            if "지분율" in s and "→" not in s:
                return False
            m = re.search(r"([+-]?\d+\.?\d*)\s*%p", s)
            if m:
                try:
                    if abs(float(m.group(1))) >= 5.0:
                        return False
                except ValueError:
                    pass
        return True

    # 미파싱(파싱 대상인데 detail 없음) + 중요 공시 6규칙 판정 — 단일
    # 사전 패스로 item 에 주석(_sig/_unparsed)을 박아 pill 카운트와 카드
    # 렌더가 같은 값을 읽게(이중 계산·불일치 방지, 사용자 2026-06-12).
    # 자기주식 3% 규칙만 발행주식수 필요 → 해당 카드에서만 FSC 조회(메모).
    # 유상증자 시총 5% 규칙(2026-06-12)은 시총 numeric — 같은 FSC 12h 캐시.
    from bot import dart_feed as _dart_feed
    _sh_memo: dict[str, float | None] = {}
    _mcw_memo: dict[str, float | None] = {}

    def _shares_for(code: str):
        if not (code and len(code) == 6 and code.isdigit()):
            return None
        if code not in _sh_memo:
            if _time.time() > _mc_deadline:
                return None
            try:
                _sh_memo[code] = _dart_feed._shares_outstanding(code)
            except Exception:
                _sh_memo[code] = None
        return _sh_memo[code]

    def _mcap_for(code: str):
        if not (code and len(code) == 6 and code.isdigit()):
            return None
        if code not in _mcw_memo:
            if _time.time() > _mc_deadline:
                return None
            try:
                _mcw_memo[code] = _dart_feed._market_cap_won(code)
            except Exception:
                _mcw_memo[code] = None
        return _mcw_memo[code]

    def _annotate(it: dict) -> None:
        if "_sig" in it:
            return
        sig = None
        try:
            rn0 = it.get("report_nm", "")
            # 자사주취득·소각 = 발행주식 3% 비율 판정 → shares 필요
            if (("자기주식" in rn0 and "취득" in rn0 and "결정" in rn0)
                    or "소각" in rn0):
                sig = _dart_feed.significance(
                    it, shares_outstanding=_shares_for(it.get("stock_code", "")))
            elif "유상증자" in rn0:
                # 시총대비 5% 판정 → mcap 필요 (FSC 12h 캐시 공유)
                sig = _dart_feed.significance(
                    it, market_cap=_mcap_for(it.get("stock_code", "")))
            else:
                sig = _dart_feed.significance(it)
        except Exception:
            sig = None
        it["_sig"] = sig
        # 미파싱 = 파싱 대상인데 의미있는 detail 부재(시총/주요사업 줄 제외).
        # '의도된 미파싱'(미파싱제외 — freeform/첨부정정)과 '진짜 미파싱'(파서 갭)
        # 분리 (사용자 2026-06-14). 진짜만 _unparsed, 의도는 _noparse.
        try:
            meaningful = [l for l in (it.get("detail") or [])
                          if not str(l).startswith("주요사업:")
                          and "시가총액" not in str(l)]
            _base_unp = bool(_dart_feed.is_parse_target(it)) and not meaningful
            if _base_unp and _dart_feed.intended_freeform_unparsed(
                    it.get("report_nm", "")):
                it["_unparsed"], it["_noparse"] = False, True
            else:
                it["_unparsed"], it["_noparse"] = _base_unp, False
        except Exception:
            it["_unparsed"] = it["_noparse"] = False

    cat_counts: dict[str, int] = {}
    _sig_total = _unp_total = _noparse_total = 0
    _filtered_total = 0
    for items in by_date.values():
        for it in items:
            if not _equity_noise(it):
                _annotate(it)
                if it.get("_sig"):
                    _sig_total += 1
                if it.get("_unparsed"):
                    _unp_total += 1
                if it.get("_noparse"):
                    _noparse_total += 1
    for items in by_date.values():
        for it in items:
            if _equity_noise(it):
                continue
            _filtered_total += 1
            c = it.get("category", "기타")
            cat_counts[c] = cat_counts.get(c, 0) + 1

    pills: list[str] = []
    pills.append(f'<button class="df-pill active" data-cat="전체">전체 {_filtered_total}</button>')
    # 특수 플래그 필터(사용자 2026-06-12) — data-flag 기준, 카테고리와 별개.
    if _sig_total:
        pills.append(f'<button class="df-pill df-pill-sig" data-flag="sig">🔥 중요 {_sig_total}</button>')
    if _unp_total:
        pills.append(f'<button class="df-pill df-pill-unp" data-flag="unparsed">⚠️ 미파싱 {_unp_total}</button>')
    if _noparse_total:
        pills.append(f'<button class="df-pill df-pill-noparse" data-flag="noparse">미파싱제외 {_noparse_total}</button>')
    for cat in _DART_CATEGORIES[1:]:
        n = cat_counts.get(cat, 0)
        if n > 0:
            pills.append(f'<button class="df-pill" data-cat="{cat}">{cat} {n}</button>')

    # 테마(다크/라이트) + df-* CSS 를 컨텐츠 전에 emit → FOUC('처음에 깨졌다
    # 복귀') 방지. CSS 가 카드 1000+개 뒤에 있으면 무스타일·전체펼침 첫
    # paint 후 스냅(2026-06-10 잔존 건 — _DART_FEED_CSS 주석 참조).
    parts: list[str] = [_SCREENER_CSS, "<script>" + _THEME_JS + "</script>",
                        _DART_FEED_CSS]
    # 관계후보 발굴 비용(kg_dart) — 공시 수집·파싱은 무료(공공 API)지만 계약공시
    # 본문 관계추출은 Gemini라 ₩0 아님(사용자 2026-06-24). 블로그 발굴분(kg_blog)은
    # blog.html 카드로 분리 — 각 대시보드가 자기 비용만. 메인 '관계후보'는 둘 합산.
    _df_kg_t, _df_kg_m = _kg_candidate_cost_usd(kinds={"kg_dart"})
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="market.html">🌍 홈</a>
    · <a href="index.html">🦉 종목분석</a>
  </div>
  <h1>DART 공시</h1>
  <p class="sub">출처 DART(OpenDART) · 1분 수집 · {datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")} 기준</p>
  <p class="sub" style="margin:-6px 0 14px">🔗 관계후보 발굴 비용(계약공시 본문, Gemini): 오늘 {_krw(_df_kg_t)} · 이번 달 {_krw(_df_kg_m)} <span style="opacity:.7">— 공시 수집·파싱은 무료, 본문 관계추출만 과금. 메인 '관계후보'에 합산</span></p>

  <div class="df-controls">
    <div class="df-pills">{''.join(pills)}</div>
    <div class="df-right">
      <div class="search-bar" style="margin:0">
        <input id="df-search" type="text" placeholder="종목명 · 공시내용 검색" autocomplete="off" spellcheck="false">
        <button id="df-clear" type="button" title="초기화">초기화</button>
      </div>
      <div class="df-view-btns">
        <button class="df-vbtn active" data-view="grid" title="그리드">
          <svg width="16" height="16" viewBox="0 0 16 16"><rect x="1" y="1" width="5" height="5" rx="1" fill="currentColor"/><rect x="10" y="1" width="5" height="5" rx="1" fill="currentColor"/><rect x="1" y="10" width="5" height="5" rx="1" fill="currentColor"/><rect x="10" y="10" width="5" height="5" rx="1" fill="currentColor"/></svg>
        </button>
        <button class="df-vbtn" data-view="list" title="리스트">
          <svg width="16" height="16" viewBox="0 0 16 16"><rect x="1" y="2" width="14" height="2.5" rx="1" fill="currentColor"/><rect x="1" y="6.75" width="14" height="2.5" rx="1" fill="currentColor"/><rect x="1" y="11.5" width="14" height="2.5" rx="1" fill="currentColor"/></svg>
        </button>
      </div>
      <button id="df-csv" type="button" class="df-csv-btn" title="현재 필터된 공시를 CSV(엑셀)로 내려받기 — 한국수출입 대시보드와 동일">📥 CSV</button>
    </div>
  </div>
""")
    # 색상 범례 (사용자 2026-06-12) — 🔥 중요/⚠️ 미파싱 의미 안내. 해당
    # 카드가 하나라도 있을 때만 노출(노이즈 방지).
    if _sig_total or _unp_total or _noparse_total:
        _lg = []
        if _sig_total:
            _lg.append('<span class="df-badge df-badge-sig">🔥 중요</span> '
                       '금색 — 손익(매출+20%/영업익+30%)·계약 매출10%·시설 자기자본15%·소각/자사주 발행주식3%·유상증자 시총5%·신규 5% 대량보유·배당 시가배당률3%·상장폐지')
        if _unp_total:
            _lg.append('<span class="df-badge df-badge-unp">⚠️ 미파싱</span> '
                       '파란 점선 — 우리 파서 갭(제목·원문 공유 시 파서 추가)')
        if _noparse_total:
            _lg.append('<span class="df-badge df-badge-noparse">미파싱제외</span> '
                       '회색 점선 — 설계상 구조화 대상 아님(자율공시·첨부정정, 제목이 곧 내용)')
        parts.append('<p class="sub" style="margin:-6px 0 14px">'
                     + ' &nbsp;·&nbsp; '.join(_lg) + '</p>')

    from collections import OrderedDict as _OD
    _months: "_OD[str, list]" = _OD()
    for date_str in sorted(by_date.keys(), reverse=True):
        _months.setdefault(date_str[:7], []).append(date_str)

    _date_idx = 0  # 전체 날짜 순번 — 최신 1개만 기본 펼침
    for mi, (ym, dates) in enumerate(_months.items()):
        try:
            _my, _mm = ym.split("-")
            month_label = f"{_my}년 {int(_mm)}월"
        except Exception:
            month_label = ym
        month_total = sum(len(by_date[d]) for d in dates)
        m_cls = "" if mi == 0 else " collapsed"   # 최신 월만 펼침, 과거 월 접힘
        parts.append(f"""
  <div class="df-month{m_cls}" data-month="{ym}">
    <div class="df-month-hd"><span class="df-caret">▾</span><span class="df-month-lbl">{month_label}</span><span class="df-month-cnt">{month_total}건</span></div>
    <div class="df-month-body">
""")
        for date_str in dates:
            # 접수번호(rcept_no = DART 접수시각 순서 내포) 내림차순 — 카드
            # 순서 = 실제 공시가 뜬 순서(최신 위). 아카이브 파일 순서는
            # 백필/재fetch 가 섞을 수 있어 렌더타임 정렬로 고정 (사용자
            # 2026-06-13 '업데이트 순서가 맞아야', 과거 기록 소급).
            items = sorted(by_date[date_str],
                           key=lambda x: str(x.get("rcept_no") or ""),
                           reverse=True)
            try:
                d = _dt.strptime(date_str, "%Y-%m-%d")
                wd = _WEEKDAY_KR.get(d.weekday(), "")
                label = f"{d.year}년 {d.month}월 {d.day}일 ({wd})"
            except Exception:
                label = date_str
            d_cls = "" if _date_idx == 0 else " collapsed"  # 최신 날짜만 펼침
            _date_idx += 1

            parts.append(f"""
      <div class="df-date-group{d_cls}" data-date="{date_str}">
        <div class="df-date-hd">
          <span class="df-caret">▾</span><span>{label}</span>
          <span class="df-date-meta"><span class="df-date-cnt">{len(items)}</span></span>
        </div>
        <div class="df-date-body"><div class="df-grid">
""")
            for it in items:
                cn = _html.escape(it.get("corp_name", ""))
                rn = _html.escape(it.get("report_nm", ""))
                cat = it.get("category", "기타")
                if _equity_noise(it):
                    continue
                cat_color = _DART_CAT_COLORS.get(cat, "#78909c")
                url = _html.escape(it.get("url", "#"))
                dt_short = date_str[5:]  # MM-DD
                detail_lines = it.get("detail", [])
                stock_code = it.get("stock_code", "")
                _imp_id = _html.escape(str(it.get("rcept_no") or f"{cn}|{rn}"))  # 중요마크 안정 id

                detail_html = ""
                for ln in detail_lines:
                    s = str(ln)
                    if s.startswith("주요사업:"):
                        continue
                    # 시총/현재가 줄은 df-mcap — 리스트 뷰에서 상세는 숨기고
                    # 이 줄만 유지 (사용자 2026-06-11 '제목+시총/현재가만').
                    _cls = ("df-detail-ln df-mcap" if "시가총액" in s
                            else "df-detail-ln")
                    detail_html += f'<div class="{_cls}">{_html.escape(s)}</div>'
                # 시총/현재가 — '모든' 상장사 공시 맨 아래(사용자 2026-06-11).
                # 렌더 시점 부착: 제목만 카드 + 옛 카드 소급. 옛 enrich 가
                # 이미 붙인 카드(detail 에 '시가총액' 존재)는 중복 방지.
                if (stock_code and len(stock_code) == 6 and stock_code.isdigit()
                        and not any("시가총액" in str(l) for l in detail_lines)):
                    for ln in _mc_lines(stock_code):
                        detail_html += (f'<div class="df-detail-ln df-mcap">'
                                        f'{_html.escape(str(ln))}</div>')

                ticker_link = ""
                if stock_code and len(stock_code) == 6 and stock_code.isdigit():
                    ticker_link = f' <a href="lookup/{stock_code}.KS" class="df-ticker-link">{stock_code}</a>'

                # 중요(🔥)/미파싱(⚠️) 색상 구별 — 사전 패스 주석 사용
                # (사용자 2026-06-12). 중요는 금색 테두리 + 사유 배지, 미파싱은
                # 주황 점선 테두리 + 배지. data-flag 로 pill 필터.
                _annotate(it)
                _sig = it.get("_sig")
                _unp = it.get("_unparsed")
                _nop = it.get("_noparse")
                _card_cls = "df-card"
                _flags: list[str] = []
                _badges = ""
                if _sig:
                    _card_cls += " df-significant"
                    _flags.append("sig")
                    _badges += (f'<span class="df-badge df-badge-sig">🔥 '
                                f'{_html.escape(str(_sig))}</span>')
                if _unp:
                    _card_cls += " df-unparsed"
                    _flags.append("unparsed")
                    _badges += ('<span class="df-badge df-badge-unp" '
                                'title="우리 방식으로 파싱되지 않은 카드 — 제목·원문 '
                                '링크를 공유하면 파서를 추가합니다">⚠️ 미파싱</span>')
                if _nop:
                    _card_cls += " df-noparse"
                    _flags.append("noparse")
                    _badges += ('<span class="df-badge df-badge-noparse" '
                                'title="설계상 구조화 대상이 아닌 자율공시/첨부정정 — '
                                '제목이 곧 내용(파서 갭 아님)">미파싱제외</span>')
                _flag_attr = " ".join(_flags)

                parts.append(f"""
          <div class="{_card_cls}" data-cat="{cat}" data-flag="{_flag_attr}" data-name="{cn}" data-report="{rn}" data-imp-id="{_imp_id}">
            <div class="df-card-hd">
              <a href="{url}" target="_blank" rel="noopener" class="df-corp">{cn}</a>{ticker_link}
              <span class="df-meta"><span class="df-dt">{dt_short}</span> <span class="df-cat" style="background:{cat_color}">{cat}</span></span>
            </div>
            <div class="df-report">{rn}</div>
            {_badges}
            {detail_html}
          </div>
""")
            parts.append("        </div></div>\n      </div>\n")
        parts.append("    </div>\n  </div>\n")

    # JS for filtering, search, view toggle — CSS 는 위(컨텐츠 앞) _DART_FEED_CSS.
    # ⚠️ raw 문자열(r-prefix) 필수 — CSV out.join('\r\n') 의 \r\n 이 비-raw 면 실제
    # 개행으로 변환돼 JS 문자열 리터럴이 깨짐 → IIFE 전체 SyntaxError(필터·CSV 전부
    # 사망, 2026-06-14 사용자 '필터 하나도 클릭안되고 CSV 도 안먹어'). 스크리너 CSV
    # 블록과 동일하게 raw 유지.
    parts.append(r"""
<script>
(function(){
  var pills=document.querySelectorAll('.df-pill');
  var cards=document.querySelectorAll('.df-card');
  var search=document.getElementById('df-search');
  var clearBtn=document.getElementById('df-clear');
  var viewBtns=document.querySelectorAll('.df-vbtn');
  var grids=document.querySelectorAll('.df-grid');
  var activeCat='전체';
  // 🔥 중요(sig) / ⚠️ 미파싱(unparsed) — 카테고리와 **독립 토글**, 동시
  // 선택 가능(사용자 2026-06-12 '미파싱+실적, +중요+계약'). 선택된 플래그
  // 전부 AND (교집합).
  var activeFlags=[];
  var wrap=document.querySelector('.wrap');

  function applyFilters(){
    var q=(search?search.value:'').toLowerCase().trim();
    // 텍스트 검색 중에만 접힌 그룹을 펼쳐 전 날짜 매칭을 보인다. 카테고리/플래그
    // 탭은 '전체'처럼 기본 펼침(최신일만) 유지 — 나머지 날짜는 접힘(헤더 클릭 펼침,
    // 일자별 매칭 카운트 표시). 사용자 2026-06-15 '다른 탭들도 전체탭처럼 해당일만'.
    if(wrap)wrap.classList.toggle('df-searching', !!q);
    // ★중요/📝메모 필터(_IMPORTANT_BLOCK, html.imp-only/memo-only)를 카테고리·검색과
    // 교집합(intersection)으로 합성: 마크 필터가 켜지면 비마크 카드도 .hidden 처리해
    // 그룹 카운트·표시(.df-card:not(.hidden) 기준)가 정확히 줄어든다. 안 그러면
    // imp-only CSS 가 카드만 숨기고 카운트엔 안 잡혀 '실적 누르고 중요 누르면
    // 빈 그룹·틀린 카운트'(사용자 2026-06-27). 토글 시 impfilterchange 로 재적용.
    var de=document.documentElement;
    var impOnly=de.classList.contains('imp-only');
    var memoOnly=de.classList.contains('memo-only');
    cards.forEach(function(c){
      var catMatch=activeCat==='전체'||c.dataset.cat===activeCat;
      var cf=(c.dataset.flag||'');
      var flagMatch=activeFlags.every(function(f){return cf.indexOf(f)>=0;});
      var markMatch=(!impOnly||c.classList.contains('imp-on'))&&
                    (!memoOnly||c.classList.contains('has-memo'));
      if(!flagMatch||!markMatch){c.classList.add('hidden');return;}
      var textMatch=!q||
        (c.dataset.name||'').toLowerCase().indexOf(q)>=0||
        (c.dataset.report||'').toLowerCase().indexOf(q)>=0||
        c.textContent.toLowerCase().indexOf(q)>=0;
      if(catMatch&&textMatch){c.classList.remove('hidden')}
      else{c.classList.add('hidden')}
    });
    document.querySelectorAll('.df-date-group').forEach(function(g){
      var n=g.querySelectorAll('.df-card:not(.hidden)').length;
      var cnt=g.querySelector('.df-date-cnt');
      if(cnt)cnt.textContent=n;
      g.style.display=n?'':'none';
    });
    document.querySelectorAll('.df-month').forEach(function(m){
      var n=m.querySelectorAll('.df-card:not(.hidden)').length;
      // 월 카운트도 필터 동기화 — 안 그러면 일 카운트(df-date-cnt)는 필터된
      // 수로 줄어드는데 월은 전체(예: 일 90 ↔ 월 4697건)로 남아 데이터가
      // 사라진 듯 보인다(사용자 2026-06-15 '심각'). 필터 없으면 전체 복원.
      var mc=m.querySelector('.df-month-cnt');
      if(mc)mc.textContent=n+'건';
      m.style.display=n?'':'none';
    });
  }

  // 월/날짜 헤더 클릭 → 접기/펼치기
  document.querySelectorAll('.df-month-hd').forEach(function(h){
    h.addEventListener('click',function(){h.parentElement.classList.toggle('collapsed');});
  });
  document.querySelectorAll('.df-date-hd').forEach(function(h){
    h.addEventListener('click',function(){h.parentElement.classList.toggle('collapsed');});
  });
  pills.forEach(function(p){
    p.addEventListener('click',function(){
      if(p.dataset.flag){
        // 플래그 pill(🔥/⚠️) — 독립 토글, 카테고리·다른 플래그와 공존
        p.classList.toggle('active');
        var f=p.dataset.flag, i=activeFlags.indexOf(f);
        if(i>=0)activeFlags.splice(i,1); else activeFlags.push(f);
      }else{
        // 카테고리 pill — 카테고리끼리만 상호배타(플래그는 유지)
        pills.forEach(function(x){if(!x.dataset.flag)x.classList.remove('active')});
        p.classList.add('active');
        activeCat=p.dataset.cat||'전체';
      }
      applyFilters();
    });
  });
  if(search)search.addEventListener('input',applyFilters);
  // ★중요/📝메모 필터 토글(_IMPORTANT_BLOCK) → 카테고리·검색과 교집합 재적용.
  document.addEventListener('impfilterchange',applyFilters);
  if(clearBtn)clearBtn.addEventListener('click',function(){
    if(search)search.value='';
    activeFlags=[]; activeCat='전체';
    pills.forEach(function(x){x.classList.remove('active');
      if(x.dataset.cat==='전체')x.classList.add('active');});
    applyFilters();
  });
  viewBtns.forEach(function(b){
    b.addEventListener('click',function(){
      viewBtns.forEach(function(x){x.classList.remove('active')});
      b.classList.add('active');
      var v=b.dataset.view;
      grids.forEach(function(g){
        if(v==='list')g.classList.add('list-view');
        else g.classList.remove('list-view');
      });
    });
  });
  // 📥 CSV 내보내기 (사용자 2026-06-13 '다트는 한국수출입처럼 엑셀로') —
  // 현재 필터(.hidden 제외)된 공시만, 클라이언트사이드 CSV(엑셀은 UTF-8 BOM
  // 으로 한글 깨짐 방지). 서버 endpoint 불요. 한국수출입 csv-btn 패턴 미러.
  function noahCsv(filename, header, rows){
    var esc=function(v){return '"'+(v==null?'':(''+v)).replace(/"/g,'""')+'"';};
    var out=[header.map(esc).join(',')];
    rows.forEach(function(r){out.push(r.map(esc).join(','));});
    var blob=new Blob(["﻿"+out.join('\r\n')],{type:'text/csv;charset=utf-8;'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download=filename;document.body.appendChild(a);a.click();
    setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},120);
  }
  var csvBtn=document.getElementById('df-csv');
  if(csvBtn)csvBtn.addEventListener('click',function(){
    var FLAG={sig:'중요',unparsed:'미파싱',noparse:'미파싱제외'};
    var rows=[];
    cards.forEach(function(c){
      if(c.classList.contains('hidden'))return;          // 필터된 것만
      var dt=(c.querySelector('.df-dt')||{}).textContent||'';
      var flag=(c.dataset.flag||'').split(' ').map(function(f){return FLAG[f]||'';})
                 .filter(Boolean).join('/');
      var a=c.querySelector('.df-corp');
      // 파서 내용(구조화 상세) — 카드의 .df-detail-ln 줄을 ' / '로 합침
      // (사용자 2026-06-14 '다트 CSV 에 파서내용도'). 미파싱 카드는 빈 칸.
      var detail=Array.prototype.slice.call(c.querySelectorAll('.df-detail-ln'))
                   .map(function(e){return (e.textContent||'').trim();})
                   .filter(Boolean).join(' / ');
      rows.push([dt.trim(),(c.dataset.name||'').trim(),(c.dataset.cat||'').trim(),
                 (c.dataset.report||'').trim(),detail,flag,a?a.href:'']);
    });
    if(!rows.length){alert('내보낼 공시가 없습니다 (필터 확인).');return;}
    var d=new Date(),p=function(n){return(''+n).padStart(2,'0');};
    noahCsv('DART공시_'+d.getFullYear()+p(d.getMonth()+1)+p(d.getDate())+'.csv',
            ['날짜','회사','카테고리','제목','내용','플래그','원문URL'],rows);
  });
})();
</script>
""")

    parts.append("</div>\n")
    parts.append(_imp_cfg("dart", card_sel=".df-card", head_sel=".df-card-hd",
                          search_sel="#df-search"))
    parts.append(_IMPORTANT_BLOCK)
    parts.append("</body></html>")
    return "\n".join(parts)


def regenerate_dart_feed_index() -> None:
    """DART feed archive → dart_feed.html."""
    try:
        by_date = _load_dart_feed_data(days_back=30)
        html = _inject_update_banner(_render_dart_feed_page(by_date))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        (ARCHIVE_ROOT / "dart_feed.html").write_text(html, encoding="utf-8")
        total = sum(len(v) for v in by_date.values())
        log.info("dashboard: dart_feed.html regenerated (%d disclosures)", total)
    except Exception as exc:
        log.warning("dashboard: dart_feed regen failed: %s", exc)


# ═════════════════════════════════════════════════════════════════════
# Market Overview landing page (market.html)
# ═════════════════════════════════════════════════════════════════════

_MARKET_CSS = (
    "<!doctype html><html lang='ko'><head><meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<meta name='color-scheme' content='light dark'>"
    "<title>🌍 Market Overview</title>"
    "<script>" + _THEME_JS + "</script>"
    "<style>"
    ":root{--bg:#f7f8f9;--card:#fff;--border:#e8e8ea;--text:#282a30;"
    "--muted:#8a8f98;--accent:#5e6ad2;--pos:#059669;--neg:#dc2626;"
    "--neu:#8a8f98;--surface-tint:rgba(0,0,0,.05);"
    "--accent-soft:rgba(94,106,210,.07)}"
    ":root[data-theme='dark']{--bg:#0b0c0e;--card:#141518;--border:#26272b;"
    "--text:#e2e3e6;--muted:#8a8f98;--accent:#7c84e8;--pos:#10B981;"
    "--neg:#EF4444;--neu:#6B7280;--surface-tint:rgba(255,255,255,.04);"
    "--accent-soft:rgba(124,132,232,.06)}"
    "*{box-sizing:border-box}"
    "body{background:var(--bg);color:var(--text);margin:0;"
    "font-family:-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;"
    "line-height:1.55;-webkit-font-smoothing:antialiased}"
    ".wrap{max-width:1100px;margin:0 auto;padding:24px 16px 64px}"
    ".nav{margin-bottom:12px}"
    ".nav a{color:var(--accent);text-decoration:none;font-size:13px}"
    ".nav a:hover{text-decoration:underline}"
    "h1{font-size:22px;margin:0 0 4px}"
    ".sub{color:var(--muted);font-size:13px;margin:0 0 24px}"
    ".search-box{margin:20px 0 28px;display:flex;gap:8px}"
    ".search-box input{flex:1;padding:10px 14px;font-size:15px;"
    "border:1px solid var(--border);border-radius:8px;"
    "background:var(--card);color:var(--text);outline:none}"
    ".search-box input:focus{border-color:var(--accent);"
    "box-shadow:0 0 0 3px var(--accent-soft)}"
    ".search-box button{padding:10px 20px;font-size:14px;font-weight:600;"
    "border:none;border-radius:8px;background:var(--accent);color:#fff;"
    "cursor:pointer}"
    ".search-box button:hover{opacity:.9}"
    ".section-hd{display:flex;align-items:baseline;gap:10px;"
    "margin:32px 0 14px}"
    ".section-hd h2{font-size:17px;margin:0}"
    ".section-hd .ts{color:var(--muted);font-size:12px}"
    ".card-grid{display:grid;"
    "grid-template-columns:repeat(auto-fill,minmax(300px,1fr));"
    "gap:14px;margin-bottom:28px}"
    ".mcard{background:var(--card);border:1px solid var(--border);"
    "border-radius:12px;padding:14px 16px}"
    ".mcard-title{font-size:13px;font-weight:700;margin-bottom:10px;"
    "color:var(--text);padding-bottom:6px;border-bottom:1px solid var(--border)}"
    ".mcard table{width:100%;border-collapse:collapse;font-size:13px}"
    ".mcard td{padding:3px 0}"
    ".mcard td:first-child{color:var(--muted)}"
    ".mcard td:nth-child(2){text-align:right;font-weight:600;"
    "font-variant-numeric:tabular-nums}"
    ".mcard td:nth-child(3){text-align:right;width:90px;"
    "font-variant-numeric:tabular-nums;font-size:12px}"
    ".up{color:var(--pos)}.dn{color:var(--neg)}"
    ".tbl-wrap{overflow-x:auto;margin-bottom:28px}"
    ".dtbl{width:100%;border-collapse:collapse;font-size:13px}"
    ".dtbl th{text-align:left;padding:8px 10px;font-size:12px;"
    "color:var(--muted);border-bottom:2px solid var(--border);"
    "font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}"
    ".dtbl th:hover{color:var(--accent)}"
    ".dtbl th::after{content:' \\2195';font-size:9px;opacity:.4;font-weight:400}"
    ".dtbl th.sort-asc::after{content:' \\25B2';font-size:9px;color:var(--accent);opacity:1}"
    ".dtbl th.sort-desc::after{content:' \\25BC';font-size:9px;color:var(--accent);opacity:1}"
    ".dtbl td{padding:7px 10px;border-bottom:1px solid var(--surface-tint)}"
    ".dtbl tr:hover{background:var(--accent-soft)}"
    ".dtbl .sym{font-weight:600}"
    ".dtbl .sym a{color:var(--text);text-decoration:none}"
    ".dtbl .sym a:hover{color:var(--accent);text-decoration:underline}"
    ".dtbl .sym .co-name{display:block;font-size:11px;font-weight:400;color:var(--muted);margin-top:1px}"
    ".tbl-filter{margin-bottom:10px;display:flex;align-items:center;gap:8px}"
    ".tbl-filter input{padding:6px 12px;font-size:13px;border:1px solid var(--border);"
    "border-radius:6px;background:var(--card);color:var(--text);outline:none;width:240px}"
    ".tbl-filter input:focus{border-color:var(--accent)}"
    ".tbl-filter .cnt{color:var(--muted);font-size:12px}"
    # 날짜 드롭다운 필터(실적·리서치·관심종목, 사용자 2026-06-15 '스크롤 내려서
    # 날짜 선택 — 관심종목처럼'). select 로 가용 날짜만 픽(타이핑 0).
    ".tbl-filter .dfilt,.fav-ctrl .dfilt{width:auto;padding:5px 8px;font-size:12px;"
    "border:1px solid var(--border);border-radius:6px;background:var(--card);"
    "color:var(--text);cursor:pointer;outline:none}"
    ".tbl-filter .dsep,.fav-ctrl .dsep{color:var(--muted);font-size:12px}"
    ".tabs{display:flex;gap:4px;margin-bottom:14px}"
    ".tab-btn,.etab-btn{padding:6px 16px;font-size:13px;font-weight:600;"
    "border:1px solid var(--border);border-radius:6px;"
    "background:var(--card);color:var(--muted);cursor:pointer}"
    ".tab-btn.active,.etab-btn.active{background:var(--accent);color:#fff;"
    "border-color:var(--accent)}"
    ".tab-pane{display:none}.tab-pane.active{display:block}"
    ".etab-pane{display:none}.etab-pane.active{display:block}"
    ".empty-msg{color:var(--muted);font-size:13px;padding:20px 0}"
    ".show-more-btn{display:block;margin:8px auto 0;padding:6px 20px;"
    "font-size:13px;color:var(--accent);background:none;"
    "border:1px solid var(--border);border-radius:6px;cursor:pointer}"
    ".show-more-btn:hover{background:var(--accent-soft)}"
    # ── Macro snapshot ──
    ".macro-sub{color:var(--muted);font-size:12px;font-weight:600;"
    "margin:18px 0 10px;text-transform:none}"
    ".macro-grid{display:grid;"
    "grid-template-columns:repeat(auto-fill,minmax(190px,1fr));"
    "gap:12px;margin-bottom:20px}"
    ".macard{background:var(--card);border:1px solid var(--border);"
    "border-radius:12px;padding:13px 14px 8px}"
    ".macard .ml{font-size:12px;color:var(--muted);margin-bottom:6px;"
    "display:flex;align-items:center;gap:5px}"
    ".macard .mv{font-size:21px;font-weight:700;letter-spacing:-.5px;"
    "font-variant-numeric:tabular-nums;display:flex;align-items:baseline;gap:7px}"
    ".macard .mc{font-size:12px;font-weight:600;font-variant-numeric:tabular-nums}"
    ".macard .spark{margin-top:8px;display:block;width:100%;height:34px}"
    ".macard .ref{font-size:10px;color:var(--muted);font-weight:500;"
    "border:1px solid var(--border);border-radius:4px;padding:0 4px}"
    ".macard .msp{margin-left:auto;font-size:9px;color:var(--muted);"
    "border:1px solid var(--border);border-radius:3px;padding:0 4px;font-weight:500}"
    ".chart-row{display:grid;grid-template-columns:1.2fr 1.2fr .8fr;"
    "gap:14px;margin-bottom:24px}"
    ".chart-card{background:var(--card);border:1px solid var(--border);"
    "border-radius:12px;padding:14px 16px}"
    ".chart-card h3{font-size:14px;margin:0 0 4px}"
    ".chart-card .leg{font-size:11px;color:var(--muted);margin-bottom:8px;"
    "display:flex;flex-wrap:wrap;gap:10px}"
    ".chart-card .leg span{display:inline-flex;align-items:center;gap:4px}"
    ".chart-card .leg i{width:9px;height:9px;border-radius:2px;display:inline-block}"
    ".chart-card .foot{font-size:10px;color:var(--muted);margin-top:8px}"
    ".chart-card svg{display:block;width:100%;height:auto;color:var(--muted)}"
    "@media(max-width:860px){.chart-row{grid-template-columns:1fr}}"
    # ── 투자자 예탁금·신용 (deposit) ──
    ".dp-wrap{display:flex;align-items:center;flex-wrap:wrap;gap:10px 22px;"
    "background:var(--card);border:1px solid var(--border);border-radius:12px;"
    "padding:12px 16px;margin-bottom:14px}"
    ".dp-hd{font-size:13px;font-weight:700}"
    ".dp-item{display:flex;align-items:baseline;gap:7px}"
    ".dp-l{font-size:12px;color:var(--muted)}"
    ".dp-v{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}"
    ".dp-ts{font-size:11px;color:var(--muted);margin-left:auto}"
    # ── 업종 등락 TOP (sector movers) ──
    ".sm-wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:24px}"
    "@media(max-width:700px){.sm-wrap{grid-template-columns:1fr}}"
    ".sm-col{background:var(--card);border:1px solid var(--border);"
    "border-radius:12px;padding:14px 16px}"
    ".sm-hd{font-size:13px;font-weight:700;margin-bottom:8px;padding-bottom:6px;"
    "border-bottom:1px solid var(--border)}"
    ".sm-tbl{width:100%;border-collapse:collapse;font-size:13px}"
    ".sm-tbl td{padding:5px 0;font-variant-numeric:tabular-nums}"
    ".sm-tbl td:first-child{color:var(--text)}"
    ".sm-tbl td:last-child{text-align:right;font-weight:600;width:84px}"
    ".sm-tbl .rk{color:var(--muted);width:22px;font-size:12px}"
    # ── Market Daily cards ──
    ".md-row{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;margin-bottom:28px}"
    "@media(max-width:700px){.md-row{grid-template-columns:1fr}}"
    ".md-card{background:var(--card);border:1px solid var(--border);"
    "border-radius:12px;padding:18px 20px;display:flex;flex-direction:column}"
    ".md-card .md-label{font-size:11px;letter-spacing:1.5px;color:var(--muted);"
    "font-weight:600;text-transform:uppercase;margin-bottom:4px}"
    ".md-card .md-time{font-size:11px;color:var(--muted);float:right}"
    ".md-card h3{font-size:15px;font-weight:700;margin:0 0 10px;line-height:1.4}"
    ".md-card .md-body{font-size:13px;line-height:1.7;color:var(--text);"
    "max-height:260px;overflow:hidden;position:relative;flex:1}"
    ".md-card .md-body.fade::after{content:'';position:absolute;bottom:0;"
    "left:0;right:0;height:50px;background:linear-gradient(transparent,var(--card))}"
    ".md-card .md-tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}"
    ".md-tag{font-size:11px;padding:3px 8px;border-radius:4px;"
    "background:var(--surface-tint);white-space:nowrap}"
    ".md-tag.pos{background:rgba(5,150,105,.12);color:var(--pos)}"
    ".md-tag.neg{background:rgba(220,38,38,.12);color:var(--neg)}"
    ".md-more{display:inline-block;margin-top:10px;font-size:12px;"
    "color:var(--accent);text-decoration:none}"
    ".md-more:hover{text-decoration:underline}"
    ".md-empty{color:var(--muted);font-size:13px;padding:20px 0}"
    # ── Favorites table ──
    "#fav-section{margin-top:28px}"
    "#fav-section .fav-hd{display:flex;align-items:baseline;gap:10px;margin:0 0 14px}"
    "#fav-body{overflow-x:auto}"
    # 관심종목 페이지네이션(10개/페이지 + 전체 펼치기, 사용자 2026-06-16)
    ".fav-pager{display:flex;flex-wrap:wrap;gap:4px;justify-content:center;margin-top:10px}"
    ".fav-pg{min-width:30px;padding:4px 9px;border:1px solid var(--border);border-radius:6px;"
    "background:var(--surface);color:var(--text);font-size:12px;cursor:pointer}"
    ".fav-pg.active{background:var(--accent);color:#fff;border-color:var(--accent)}"
    ".fav-pg:hover{background:var(--surface-2)}"
    "#fav-section .fav-hd h2{font-size:17px;margin:0}"
    "#fav-section .fav-hd .cnt{color:var(--muted);font-size:12px}"
    ".fav-del{background:none;border:none;color:var(--neg);cursor:pointer;"
    "font-size:14px;padding:2px 6px;border-radius:4px}"
    ".fav-del:hover{background:rgba(220,38,38,.1)}"
    "#fav-tbl th.fav-sort{cursor:pointer;user-select:none;white-space:nowrap}"
    "#fav-tbl th.fav-sort:hover{color:var(--accent,#3b82f6)}"
    "#fav-tbl .fav-ar{color:var(--accent,#3b82f6);font-size:10px}"
    ".fav-ctrl{margin-bottom:10px;display:flex;gap:8px;align-items:center}"
    ".fav-ctrl select{padding:5px 10px;border-radius:6px;background:var(--card);"
    "color:var(--text);border:1px solid var(--border);font-size:13px}"
    ".fav-ord{white-space:nowrap}"
    ".fav-ord button{background:none;border:1px solid var(--border);color:var(--muted);"
    "cursor:pointer;border-radius:4px;width:22px;height:22px;font-size:10px;line-height:1;"
    "padding:0;margin:0 1px}"
    ".fav-ord button:hover{color:var(--accent,#3b82f6);border-color:var(--accent,#3b82f6)}"
    "</style></head><body>"
)


def _fmt_price(v: float, label: str = "") -> str:
    """Format a price/index value for the market snapshot cards."""
    if v is None:
        return "—"
    lab = label.lower()
    if "환율" in lab or "/달러" in lab or "/위안" in lab or "/원" in lab or "eurusd" in lab or "gbpusd" in lab:
        return f"{v:,.2f}"
    if "금리" in lab or "cpi" in lab or "ppi" in lab or "실업률" in lab or "ffr" in lab:
        return f"{v:.2f}"
    if abs(v) >= 100:
        return f"{v:,.2f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def _chg_cell(chg: float | None, pct: float | None) -> str:
    """Render a change cell with ▲/▼ and color."""
    if chg is None and pct is None:
        return '<td class="up">—</td>'
    parts = []
    if pct is not None:
        arrow = "▲" if pct >= 0 else "▼"
        cls = "up" if pct >= 0 else "dn"
        parts.append(f'{arrow}{abs(pct):.2f}%')
    elif chg is not None:
        arrow = "▲" if chg >= 0 else "▼"
        cls = "up" if chg >= 0 else "dn"
        parts.append(f'{arrow}{abs(chg):.2f}')
    else:
        cls = "neu"
    return f'<td class="{cls}">{" ".join(parts)}</td>'


def _render_market_card(title: str, items: list, yf: dict) -> str:
    """Render one market snapshot card."""
    rows: list[str] = []
    for label, tk in items:
        d = yf.get(tk)
        if not d:
            rows.append(f'<tr><td>{_html.escape(label)}</td>'
                        f'<td>—</td><td class="neu">—</td></tr>')
            continue
        val_str = _fmt_price(d.get("close"), label)
        rows.append(f'<tr><td>{_html.escape(label)}</td>'
                    f'<td>{val_str}</td>'
                    f'{_chg_cell(d.get("change"), d.get("pct"))}</tr>')
    return (f'<div class="mcard"><div class="mcard-title">'
            f'{_html.escape(title)}</div><table>{"".join(rows)}</table></div>')


def _render_fred_card(fred_data: list, dollar_idx: dict | None) -> str:
    """Render the FRED indicators card."""
    from bot.macro_snapshot import _fmt_asof   # 기준월 포맷 공유(사용자 2026-06-24)
    rows: list[str] = []
    if dollar_idx:
        val_str = f'{dollar_idx["close"]:.2f}'
        rows.append(f'<tr><td>달러 인덱스</td><td>{val_str}</td>'
                    f'{_chg_cell(dollar_idx.get("change"), dollar_idx.get("pct"))}</tr>')
    for item in fred_data:
        d = item.get("data")
        label = item.get("label", "")
        unit = item.get("unit", "")
        if not d or d.get("value") is None:
            rows.append(f'<tr><td>{_html.escape(label)}</td>'
                        f'<td>—</td><td class="neu">—</td></tr>')
            continue
        v = d["value"]
        if unit == "M":
            val_str = f'{v / 1000:.1f}M'
        elif unit == "K":
            val_str = f'{v:,.0f}K'
        elif unit == "%":
            val_str = f'{v:.2f}%'
        else:
            val_str = f'{v:,.0f}'
        chg = d.get("change")
        if chg is not None:
            arrow = "▲" if chg >= 0 else "▼"
            cls = "up" if chg >= 0 else "dn"
            chg_str = f'{arrow}{abs(chg):.2f}'
            chg_cell = f'<td class="{cls}">{chg_str}</td>'
        else:
            chg_cell = '<td class="neu">—</td>'
        # 기준월 라벨 — 헤드라인이 어느 관측월 값인지(FRED 발표지표, 사용자 2026-06-24).
        _asof = _fmt_asof(d.get("time", ""))
        _asof_html = (f' <span style="font-size:9px;color:var(--muted)">({_html.escape(_asof)})</span>'
                      if _asof else "")
        rows.append(f'<tr><td>{_html.escape(label)}</td>'
                    f'<td>{val_str}{_asof_html}</td>{chg_cell}</tr>')
    return ('<div class="mcard"><div class="mcard-title">'
            '핵심 지표 (금리/경제)</div><table>'
            + "".join(rows) + '</table></div>')


def _render_earnings_table(earnings: list) -> str:
    """Render the upcoming earnings calendar table."""
    if not earnings:
        return '<div class="empty-msg">실적 발표 일정이 없습니다.</div>'
    rows: list[str] = []
    shown = 0
    for e in earnings:
        if shown >= 100:  # 각자 최대한 — data-limit=10 으로 처음 10개만, 더보기로 전체
            break
        sym = _html.escape(e.get("symbol", ""))
        co_name = _html.escape(e.get("name", ""))
        dt = _html.escape(e.get("date", ""))
        is_kr = sym.endswith((".KS", ".KQ"))
        # JP/TW/CN/HK — yfinance .calendar 추정치가 통화/단위 불명확(거의
        # None) → 잘못된 '$' 표기 대신 '—'. 날짜가 핵심 값(사용자 2026-06-13).
        is_intl = sym.endswith((".T", ".TW", ".TWO", ".SS", ".SZ", ".HK"))
        hour = e.get("hour", "")
        hour_label = "장전" if hour == "bmo" else ("장후" if hour == "amc" else "—")
        eps_est = e.get("eps_estimate")
        if eps_est is None or is_intl:
            eps_str = "—"
        elif is_kr:
            eps_str = f'₩{eps_est:,.0f}'
        else:
            eps_str = f'${eps_est:.2f}'
        rev_est = e.get("revenue_estimate")
        if rev_est is None or is_intl:
            rev_str = "—"
        elif is_kr:
            if rev_est >= 1e12:
                rev_str = f'₩{rev_est / 1e12:.1f}조'
            elif rev_est >= 1e8:
                rev_str = f'₩{rev_est / 1e8:,.0f}억'
            else:
                rev_str = f'₩{rev_est:,.0f}'
        elif rev_est >= 1e9:
            rev_str = f'${rev_est / 1e9:.1f}B'
        elif rev_est >= 1e6:
            rev_str = f'${rev_est / 1e6:.0f}M'
        else:
            rev_str = f'${rev_est:,.0f}'
        q = e.get("quarter")
        y = e.get("year")
        q_str = f'Q{q} {y}' if q and y else "—"
        lookup_url = f'lookup/{sym}'
        # KR/US=회사명(종목코드 생략, 사용자 2026-06-10). intl(JP/CN/HK/TW)=
        # 비-KR(미국 포함, 사용자 2026-06-15)=티커+(한글명). 한글명 미확보 시 티커만.
        # KR 은 종목명만(한글이라 자명). US co_name 은 위에서 네이버 한글명으로 주입.
        if (not is_kr) and co_name and co_name != sym:
            display = f'{sym} <span class="ts">({co_name})</span>'
        else:
            display = co_name if co_name else sym
        rows.append(
            f'<tr><td class="sym"><a href="{lookup_url}">{display}</a></td>'
            f'<td>{dt}</td><td>{hour_label}</td>'
            f'<td>{q_str}</td><td>{eps_str}</td><td>{rev_str}</td></tr>'
        )
        shown += 1
    return (
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>종목</th><th>날짜</th><th>시간</th>'
        '<th>분기</th><th>EPS 예상</th><th>매출 예상</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_research_kr_table(research: list) -> str:
    """Render the KR 기업(종목) research actions table — 일주일치."""
    if not research:
        return '<div class="empty-msg">최근 리서치 액션이 없습니다.</div>'
    rows: list[str] = []
    for r in research[:200]:
        code = _html.escape(r.get("code", ""))
        name = _html.escape(r.get("name", ""))
        broker = _html.escape(r.get("broker", ""))
        rating = _html.escape(r.get("rating", ""))
        title = _html.escape(r.get("title", ""))
        dt = _html.escape(r.get("date", ""))
        link = _html.escape(r.get("link", ""))
        target = r.get("target")
        try:
            tp_str = f'{int(float(target)):,}' if target else '—'
        except (TypeError, ValueError):
            tp_str = '—'
        lookup_url = f'lookup/{code}.KS'
        title_cell = (f'<a href="{link}" target="_blank" rel="noopener" '
                      f'style="color:var(--text);text-decoration:underline;'
                      f'text-decoration-color:var(--muted)">{title[:60]}</a>'
                      if link else title[:60])
        rows.append(
            f'<tr><td class="sym"><a href="{lookup_url}">{name or code}</a></td>'
            f'<td>{broker}</td><td>{rating}</td><td>{tp_str}</td>'
            f'<td>{title_cell}</td><td>{dt}</td></tr>'
        )
    return (
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>종목</th><th>증권사</th><th>투자의견</th><th>목표가</th>'
        '<th>제목(클릭→원문)</th><th>날짜</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_research_industry_table(research: list) -> str:
    """Render the KR 산업(업종) research table — 일주일치. 목표가 없음."""
    if not research:
        return '<div class="empty-msg">최근 산업 리포트가 없습니다.</div>'
    rows: list[str] = []
    for r in research[:200]:
        category = _html.escape(r.get("category", "") or "—")
        broker = _html.escape(r.get("broker", ""))
        title = _html.escape(r.get("title", ""))
        dt = _html.escape(r.get("date", ""))
        link = _html.escape(r.get("link", ""))
        title_cell = (f'<a href="{link}" target="_blank" rel="noopener" '
                      f'style="color:var(--text);text-decoration:underline;'
                      f'text-decoration-color:var(--muted)">{title[:60]}</a>'
                      if link else title[:60])
        rows.append(
            f'<tr><td class="sym">{category}</td>'
            f'<td>{broker}</td><td>{title_cell}</td><td>{dt}</td></tr>'
        )
    return (
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>산업</th><th>증권사</th><th>제목(클릭→원문)</th>'
        '<th>날짜</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_research_strategy_table(research: list) -> str:
    """Render the KR 전략(투자정보) research table — 일주일치. 분류·목표가
    없음(증권사·제목·날짜만). 사용자 2026-06-12 '네이버 투자전략 → 한국 전략'."""
    if not research:
        return '<div class="empty-msg">최근 전략 리포트가 없습니다.</div>'
    rows: list[str] = []
    for r in research[:200]:
        broker = _html.escape(r.get("broker", ""))
        title = _html.escape(r.get("title", ""))
        dt = _html.escape(r.get("date", ""))
        link = _html.escape(r.get("link", ""))
        title_cell = (f'<a href="{link}" target="_blank" rel="noopener" '
                      f'style="color:var(--text);text-decoration:underline;'
                      f'text-decoration-color:var(--muted)">{title[:70]}</a>'
                      if link else title[:70])
        rows.append(
            f'<tr><td>{broker}</td><td>{title_cell}</td><td>{dt}</td></tr>'
        )
    return (
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>증권사</th><th>제목(클릭→원문)</th>'
        '<th>날짜</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _research_action_badge(action: str, from_g: str, to_g: str) -> tuple[str, str]:
    """US 등급변경 종류 라벨+색 (사용자 2026-06-18 A1). from/to 비교가 1차
    (유지↔변경↔신규 robust), yfinance Action(up/down)으로 상향/하향 방향만 보강.
    '유지'(Buy→Buy 재확인)는 회색으로 묻고 실제 변동은 색으로 부각 + 라벨이 검색
    필터 키(검색박스에 '상향' 입력 → 등급변경만). 색은 다크/라이트 공용."""
    a = (action or "").strip().lower()
    f = (from_g or "").strip()
    t = (to_g or "").strip()
    if not f:
        return "신규", "#2563eb"          # 커버리지 개시(FromGrade 없음)
    if f == t:
        return "유지", "#9aa0aa"          # 재확인(Buy→Buy) — 회색 de-emphasize
    if a == "up":
        return "상향", "#16a34a"
    if a == "down":
        return "하향", "#dc2626"
    return "변경", "#d4a017"              # 등급 바뀜·방향 불명(스케일 상이)


def _render_research_us_table(research: list) -> str:
    """Render the US research actions table."""
    if not research:
        return '<div class="empty-msg">최근 리서치 액션이 없습니다.</div>'
    rows: list[str] = []
    for r in research[:40]:
        sym = _html.escape(r.get("symbol", ""))
        # 미국 종목 한글명 ( ) — 위에서 네이버 koreanCodeName 주입(name_kr). 있으면
        # '티커 (한글명)'(사용자 2026-06-15 '미국도 다른나라처럼'). 미매핑은 티커만.
        kr = _html.escape(r.get("name_kr", "") or "")
        sym_disp = (f'{sym} <span class="ts">({kr})</span>'
                    if kr and kr != sym else sym)
        firm = _html.escape(r.get("firm", ""))
        raw_from = r.get("from_grade", "") or ""
        raw_to = r.get("to_grade", "") or ""
        to_g = _html.escape(raw_to)
        from_g = _html.escape(raw_from)
        dt = _html.escape(r.get("date", ""))
        # A1 배지 — 유지(회색)↔상향/하향/신규/변경(색). 라벨이 검색 필터 키.
        b_lbl, b_col = _research_action_badge(r.get("action", ""), raw_from, raw_to)
        badge = (f'<span style="color:{b_col};font-weight:600">{b_lbl}</span> '
                 if b_lbl else '')
        grade_str = badge + (f'{from_g} → {to_g}' if from_g else to_g)
        target = r.get("target")
        if isinstance(target, str) and target.strip():
            tp_str = _html.escape(target)        # A2: Finnhub 컨센서스 분포(매수·보유·매도)
        else:
            try:
                tp_str = f'${float(target):,.0f}' if target else '—'
            except (TypeError, ValueError):
                tp_str = '—'
        lookup_url = f'lookup/{sym}'
        rows.append(
            f'<tr><td class="sym"><a href="{lookup_url}">{sym_disp}</a></td>'
            f'<td>{firm}</td><td>{grade_str}</td>'
            f'<td>{tp_str}</td><td>{dt}</td></tr>'
        )
    # 위젯은 yfinance 캡(최근 일부 종목)이라 전수가 아님 → 전체 미국 애널리스트
    # 등급변경·목표가·리서치는 외부 집계 다수를 표 **위 가로 한 줄**로 (사용자 2026-06-19
    # '위쪽으로·다른 사이트들도 이어서·최대한 많이'). 컨텍스트 참조 링크라 /sites(외부 nav)
    # 정책과 무관 — 위젯 내 데이터-소스 모음. 전부 무료 열람 가능한 등급변경/리서치 페이지.
    # 알약(pill) 칩 — accent 색·테두리 명시로 가독성↑ + 브라우저 :visited 보라색 override
    # (사용자 2026-06-19 '잘 안 보인다'). var(--accent) 테마 연동 + fallback 색.
    def _ref(url, name):
        return (f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
                'style="display:inline-block;padding:2px 10px;margin:2px 3px;'
                'border:1px solid var(--accent,#2f81f7);border-radius:12px;'
                'color:var(--accent,#2f81f7);font-weight:600;white-space:nowrap;'
                f'text-decoration:none">{name}</a>')
    refs = (
        '<div class="research-refs" style="margin:2px 2px 10px;font-size:12px;line-height:2.1">'
        '<span style="opacity:.75">📋 전체 미국 등급변경·리서치 참조</span> '
        # TipRanks·Nasdaq·StreetInsider·Yahoo 제거 (사용자 2026-06-19 '유료고 안돼').
        # 무료·접근가능한 곳만 유지.
        + ' '.join([
            _ref("https://www.marketbeat.com/ratings/", "MarketBeat"),
            _ref("https://www.benzinga.com/analyst-ratings", "Benzinga"),
            _ref("https://stockanalysis.com/", "StockAnalysis"),
        ]) + '</div>'
    )
    return (
        refs +
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>종목</th><th>증권사</th><th>등급 변경</th>'
        '<th>TP(컨센서스)</th><th>날짜</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_research_intl_table(research: list) -> str:
    """JP/TW/CN/HK 등급변경 — 한글 종목명(번역)·증권사·등급·날짜 (사용자
    2026-06-13 '보고 판단'). US 표 구조 미러, 종목=한글명(없으면 티커), TP 생략
    (intl 미수집)."""
    if not research:
        return ('<div class="empty-msg">최근 등급 변경이 없습니다. '
                '(yfinance 해외 애널리스트 커버리지 제한)</div>')
    rows: list[str] = []
    for r in research[:40]:
        sym = _html.escape(r.get("symbol", ""))
        label = _html.escape(r.get("name") or r.get("symbol", ""))
        firm = _html.escape(r.get("firm", ""))
        to_g = _html.escape(r.get("to_grade", ""))
        from_g = _html.escape(r.get("from_grade", ""))
        dt = _html.escape(r.get("date", ""))
        grade_str = f'{from_g} → {to_g}' if from_g else to_g
        rows.append(
            f'<tr><td class="sym"><a href="lookup/{sym}">{label}</a></td>'
            f'<td>{firm}</td><td>{grade_str}</td><td>{dt}</td></tr>'
        )
    return (
        '<div class="tbl-wrap" data-limit="10"><table class="dtbl">'
        '<thead><tr><th>종목</th><th>증권사</th><th>등급 변경</th>'
        '<th>날짜</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table></div>'
    )


# ── Macro Snapshot rendering (SV port — dependency-free inline SVG) ──

def _macro_spark_svg(values: list, direction=None) -> str:
    """Inline SVG sparkline. **라인=최근 1개월**(yf 일봉 ~22점; FRED/ECOS 월간).
    색 = 1개월 방향(direction: +1 상승/-1 하락/0 보합). direction 미지정 시
    series 첫↔끝(vals[-1]-vals[0])으로 폴백. 오르면 초록·내리면 빨강·보합 회색."""
    vals = [v for v in (values or []) if v is not None]
    if len(vals) < 2:
        return '<div class="spark"></div>'
    vmin, vmax = min(vals), max(vals)
    rng = (vmax - vmin) or 1.0
    n = len(vals)
    W, H, pad = 120.0, 34.0, 3.0
    pts = []
    for i, v in enumerate(vals):
        x = pad + (i / (n - 1)) * (W - 2 * pad)
        y = pad + (1 - (v - vmin) / rng) * (H - 2 * pad)
        pts.append((x, y))
    if direction is None:
        delta = vals[-1] - vals[0]  # 표시 구간(1개월) 첫↔끝
        direction = 0 if abs(delta) < rng * 0.01 else (1 if delta > 0 else -1)
    color = ("#8b95a5" if direction == 0
             else "#26a69a" if direction > 0 else "#e2574c")
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad:.1f},{H - pad:.1f} {poly} {W - pad:.1f},{H - pad:.1f}"
    return (
        f'<svg class="spark" viewBox="0 0 {W:.0f} {H:.0f}" preserveAspectRatio="none">'
        f'<polygon points="{area}" fill="{color}" opacity="0.10"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/></svg>'
    )


def _macro_fmt_value(v, dec: int, unit: str) -> str:
    """Format the big card value with currency prefix / % suffix."""
    if v is None:
        return "—"
    num = f"{v:,.0f}" if dec == 0 else f"{v:,.{dec}f}"
    if unit == "$":
        return f"${num}"
    if unit == "%":
        return f"{num}%"
    if unit == "억$":
        return f"{num}<span style='font-size:13px;color:var(--muted);margin-left:2px'>억$</span>"
    return num


def _macro_fmt_change(change, dec: int) -> str:
    """Render the change chip: ▲/▼ value, '동결' when zero, '—' when absent."""
    if change is None:
        return '<span class="mc" style="color:var(--muted)">—</span>'
    r = round(change, dec if dec > 0 else 2)
    if r == 0:        # 전월(직전 데이터점) 대비 변화 0 — '12개월 전과 같다' 오해 방지
        return '<span class="mc" style="color:var(--muted)">전월 동일</span>'
    up = change > 0
    color = "var(--pos)" if up else "var(--neg)"
    arrow = "▲" if up else "▼"
    val = f"{abs(change):,.0f}" if dec == 0 else f"{abs(change):,.{dec}f}"
    return f'<span class="mc" style="color:{color}">{arrow} {val}</span>'


def _macro_fmt_change_pct(pct) -> str:
    """변화를 % 로 — 한달단위(1개월) 카드용(사용자 2026-06-10 '숫자말고
    퍼센티지'). 하락 빨강/상승 녹색(절대값 chip 과 동일 부호 규칙)."""
    if pct is None:
        return '<span class="mc" style="color:var(--muted)">—</span>'
    if round(pct, 2) == 0:        # 같은 가격이면 '보합' 대신 0.00%(사용자 2026-06-14)
        return '<span class="mc" style="color:var(--muted)">0.00%</span>'
    up = pct > 0
    color = "var(--pos)" if up else "var(--neg)"
    arrow = "▲" if up else "▼"
    return f'<span class="mc" style="color:{color}">{arrow} {abs(pct):.2f}%</span>'


def _render_macro_card(ind: dict) -> str:
    label = _html.escape(ind.get("label", ""))
    span = _html.escape(ind.get("spark_span", ""))
    span_html = f'<span class="msp">{span}</span>' if span else ""
    val_html = _macro_fmt_value(ind.get("value"), ind.get("decimals", 2), ind.get("unit", ""))
    # 한달단위(1개월) yf 카드는 변화를 % 로(change_pct 존재), FRED/ECOS(12개월)
    # 는 절대값 그대로(사용자 2026-06-10 '한달단위는 숫자말고 퍼센티지').
    if ind.get("change_pct") is not None:
        chg_html = _macro_fmt_change_pct(ind.get("change_pct"))
    else:
        chg_html = _macro_fmt_change(ind.get("change"), ind.get("decimals", 2))
    # 색 = 표시되는 라인의 방향(첫→끝): yf 카드는 1개월 일봉이라 1개월,
    # FRED/ECOS 는 12개월 월간이라 12개월(원래대로 그래프 — 사용자 2026-06-10).
    # 라인 기간(1개월/12개월)을 카드에 작게 명시(사용자 2026-06-10).
    spark = _macro_spark_svg(ind.get("spark", []))
    # 기준월 라벨(사용자 2026-06-24) — 헤드라인 값이 어느 기간 관측치인지(ECOS/FRED
    # 발표지표). 실시간 가격 카드(asof='')는 미표시. CLAUDE.md 규칙10b(데이터 위젯=
    # 적용시각·소스 라벨) 정합.
    asof = _html.escape(ind.get("asof", ""))
    asof_html = (f'<div class="masof" style="font-size:10px;color:var(--muted);'
                 f'margin-top:2px">기준 {asof}</div>') if asof else ""
    return (
        f'<div class="macard"><div class="ml">{label}{span_html}</div>'
        f'<div class="mv"><span>{val_html}</span>{chg_html}</div>{spark}{asof_html}</div>'
    )


def _ax_fmt(v: float) -> str:
    av = abs(v)
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 100:
        return f"{v:,.0f}"
    if av >= 10:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _svg_line_chart(labels: list, series: list) -> str:
    """Generic multi-series line chart in inline SVG. Theme-aware via
    currentColor for axes/grid; each series carries its own color.

    series item: {"name", "color", "data": [floats], "axis": "L"|"R"}
    """
    if not labels or not series:
        return ""
    has_right = any(s.get("axis") == "R" for s in series)
    W, H = 520.0, 244.0
    # padL 66 — y축 라벨이 6~7자리 콤마 숫자(372,224)일 때 선두 숫자가 잘리던 것
    # 해소(사용자 2026-06-15 '안에 글씨가 짤리는듯'). text-anchor=end 가 padL-7 에
    # 끝나므로 라벨 폭(~47px)+여유를 위해 50→66.
    padL, padT, padB = 66.0, 12.0, 30.0
    padR = 58.0 if has_right else 20.0
    plotW, plotH = W - padL - padR, H - padT - padB

    def _range(axis: str):
        pool: list[float] = []
        for s in series:
            if s.get("axis", "L") == axis:
                pool.extend([v for v in s["data"] if v is not None])
        if not pool:
            return None
        lo, hi = min(pool), max(pool)
        if lo == hi:
            lo -= 1.0
            hi += 1.0
        pad = (hi - lo) * 0.12
        return lo - pad, hi + pad

    lr = _range("L")
    rr = _range("R") if has_right else None
    if lr is None:
        return ""
    n = max(len(s["data"]) for s in series)

    def _x(i: int) -> float:
        return padL + (i / (n - 1)) * plotW if n > 1 else padL + plotW / 2

    def _y(v: float, rng) -> float:
        lo, hi = rng
        return padT + (1 - (v - lo) / (hi - lo)) * plotH

    parts: list[str] = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}">']

    # horizontal gridlines + left axis ticks
    for k in range(5):
        gy = padT + (k / 4) * plotH
        parts.append(
            f'<line x1="{padL:.0f}" y1="{gy:.1f}" x2="{W - padR:.0f}" y2="{gy:.1f}" '
            f'stroke="currentColor" stroke-width="0.5" opacity="0.18"/>'
        )
        lv = lr[1] - (k / 4) * (lr[1] - lr[0])
        parts.append(
            f'<text x="{padL - 7:.0f}" y="{gy + 5:.1f}" font-size="13" '
            f'fill="currentColor" text-anchor="end">{_ax_fmt(lv)}</text>'
        )
    # right axis ticks
    if rr is not None:
        for k in range(5):
            gy = padT + (k / 4) * plotH
            rv = rr[1] - (k / 4) * (rr[1] - rr[0])
            parts.append(
                f'<text x="{W - padR + 7:.0f}" y="{gy + 5:.1f}" font-size="13" '
                f'fill="currentColor" text-anchor="start">{_ax_fmt(rv)}</text>'
            )
    # x labels (~6 evenly). 연도 prefix 제거 — "2025-07"→"07", "2025.11.28"→"11.28"
    # (사용자 2026-06-15 '연도 빼면 가독성↑, 월만으로 충분'). 짧아져 안 겹침.
    step = max(1, (n - 1) // 5) if n > 1 else 1
    for i in range(0, n, step):
        _raw = str(labels[i]) if i < len(labels) else ""
        lab = _html.escape(re.sub(r'^\d{4}[-.]', '', _raw))
        parts.append(
            f'<text x="{_x(i):.1f}" y="{H - 12:.0f}" font-size="13" '
            f'fill="currentColor" text-anchor="middle">{lab}</text>'
        )
    # series polylines
    for s in series:
        rng = rr if s.get("axis") == "R" else lr
        if rng is None:
            continue
        pts = " ".join(
            f"{_x(i):.1f},{_y(v, rng):.1f}"
            for i, v in enumerate(s["data"]) if v is not None
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{s["color"]}" '
            f'stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _chart_card(title: str, legend: list, svg: str, foot: str) -> str:
    """Wrap an SVG chart with a title, color legend, and source footnote."""
    if not svg:
        return ""
    leg = "".join(
        f'<span><i style="background:{c}"></i>{_html.escape(nm)}</span>' for nm, c in legend
    )
    return (
        f'<div class="chart-card"><h3>{_html.escape(title)}</h3>'
        f'<div class="leg">{leg}</div>{svg}'
        f'<div class="foot">{_html.escape(foot)}</div></div>'
    )


def _render_sentiment_gauge(data: dict) -> str:
    """Semicircular VIX-derived sentiment gauge (inline SVG)."""
    import math
    score = int(data.get("score", 50))
    vix = data.get("vix")
    W, H = 240.0, 158.0
    cx, cy, r = 120.0, 132.0, 96.0

    def _pt(s: float, rad: float):
        th = math.radians(180 * (1 - s / 100))
        return cx + rad * math.cos(th), cy - rad * math.sin(th)

    zones = [(0, 25, "#e2574c"), (25, 45, "#f0883e"), (45, 55, "#ffd54f"),
             (55, 75, "#9ccc65"), (75, 100, "#26a69a")]
    parts = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}">']
    for lo, hi, col in zones:
        seg = []
        s = lo
        while s <= hi + 1e-9:
            x, y = _pt(s, r)
            seg.append(f"{x:.1f},{y:.1f}")
            s += 2
        parts.append(
            f'<polyline points="{" ".join(seg)}" fill="none" stroke="{col}" '
            f'stroke-width="15" stroke-linecap="round"/>'
        )
    # needle + hub
    nx, ny = _pt(score, r - 20)
    parts.append(
        f'<line x1="{cx:.0f}" y1="{cy:.0f}" x2="{nx:.1f}" y2="{ny:.1f}" '
        f'stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>'
    )
    parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="5" fill="currentColor"/>')
    # center value + label
    from bot.macro_snapshot import sentiment_label
    lbl = sentiment_label(score)
    parts.append(
        f'<text x="{cx:.0f}" y="{cy - 24:.0f}" font-size="30" font-weight="700" '
        f'fill="currentColor" text-anchor="middle">{score}</text>'
    )
    parts.append(
        f'<text x="{cx:.0f}" y="{cy - 6:.0f}" font-size="12" '
        f'fill="currentColor" text-anchor="middle">{_html.escape(lbl)}</text>'
    )
    parts.append('</svg>')
    svg = "".join(parts)
    vix_str = f"VIX {vix:.2f} 역산" if isinstance(vix, (int, float)) else "VIX 역산"
    return (
        f'<div class="chart-card"><h3>시장 센티먼트</h3>'
        f'<div class="leg"><span>공포 0 ↔ 100 탐욕</span></div>{svg}'
        f'<div class="foot">출처: {_html.escape(vix_str)}</div></div>'
    )


def _render_deposit_widget(dep: dict) -> str:
    """고객예탁금·신용잔고 (Naver) — 업종 등락 위. 데이터 없으면 빈 문자열."""
    if not dep or dep.get("deposit") is None:
        return ""

    def _won(v) -> str:
        # 입력 단위 억원 추정 → 조/억 표기
        if v is None:
            return "—"
        if abs(v) >= 10000:
            return f"{v / 10000:,.1f}조"
        return f"{v:,.0f}억"

    def _chg(v) -> str:
        if v is None:
            return ""
        cls = "up" if v > 0 else "dn" if v < 0 else "neu"
        arrow = "▲" if v > 0 else "▼" if v < 0 else "-"
        return f' <span class="{cls}" style="font-size:12px">{arrow}{_won(abs(v))}</span>'

    date = _html.escape(str(dep.get("date", "")))
    src = _html.escape(str(dep.get("source") or "금융투자협회"))
    dv = (f'<div class="dp-item"><span class="dp-l">고객예탁금</span>'
          f'<span class="dp-v">{_won(dep.get("deposit"))}{_chg(dep.get("deposit_chg"))}</span></div>')
    cv = ""
    if dep.get("credit") is not None:
        cv = (f'<div class="dp-item"><span class="dp-l">신용잔고</span>'
              f'<span class="dp-v">{_won(dep.get("credit"))}{_chg(dep.get("credit_chg"))}</span></div>')
    ev = ""        # 주식형펀드 (있을 때만 — graceful, 사용자 2026-06-14)
    if dep.get("equity_fund") is not None:
        ev = (f'<div class="dp-item"><span class="dp-l">주식형펀드</span>'
              f'<span class="dp-v">{_won(dep.get("equity_fund"))}{_chg(dep.get("equity_fund_chg"))}</span></div>')
    return (
        '<div class="dp-wrap"><div class="dp-hd">💰 투자자 예탁금·신용</div>'
        f'{dv}{cv}{ev}<span class="dp-ts">기준일 {date} · {src}</span></div>'
    )


def _render_deposit_charts(dep: dict) -> str:
    """고객예탁금·신용잔고 6개월 추이 라인 차트 — 위젯 아래. 데이터 없으면 빈 문자열."""
    if not dep:
        return ""
    cards: list[str] = []
    src_foot = "출처: " + str(dep.get("source") or "금융투자협회")
    dser = dep.get("deposit_series", [])
    cser = dep.get("credit_series", [])
    if len(dser) >= 2:
        svg = _svg_line_chart(
            [p["d"] for p in dser],
            [{"name": "고객예탁금", "color": "#ab47bc",
              "data": [p["v"] for p in dser], "axis": "L"}])
        cards.append(_chart_card("고객예탁금 추이 (억원)",
                                 [("고객예탁금", "#ab47bc")], svg, src_foot))
    if len(cser) >= 2:
        svg = _svg_line_chart(
            [p["d"] for p in cser],
            [{"name": "신용잔고", "color": "#42a5f5",
              "data": [p["v"] for p in cser], "axis": "L"}])
        cards.append(_chart_card("신용잔고 추이 (억원)",
                                 [("신용잔고", "#42a5f5")], svg, src_foot))
    # 주식형펀드 자금동향 — 신용잔고 옆 3번째 차트 (사용자 2026-06-14). 데이터
    # (equity_fund_series) 있을 때만 → graceful 2-col 회귀. 출처는 같은 KOFIA.
    eser = dep.get("equity_fund_series", [])
    if len(eser) >= 2:
        svg = _svg_line_chart(
            [p["d"] for p in eser],
            [{"name": "주식형펀드", "color": "#26a69a",
              "data": [p["v"] for p in eser], "axis": "L"}])
        cards.append(_chart_card("주식형펀드 추이 (억원)",
                                 [("주식형펀드", "#26a69a")], svg, src_foot))
    if not cards:
        return ""
    return (f'<div class="chart-row" style="grid-template-columns:'
            f'repeat({len(cards)},1fr);margin-top:-10px">'
            + "".join(cards) + '</div>')


def _render_sector_movers(movers: dict) -> str:
    """업종 등락 TOP 10 (상승/하락) 위젯 — Naver 업종별 시세. 데이터 없으면 빈 문자열."""
    up = (movers or {}).get("up", [])
    down = (movers or {}).get("down", [])
    if not up and not down:
        return ""

    def _col(title: str, items: list) -> str:
        rows = []
        for i, s in enumerate(items, 1):
            pct = s.get("pct", 0) or 0
            cls = "up" if pct > 0 else "dn" if pct < 0 else "neu"
            sign = "+" if pct > 0 else ""
            rows.append(
                f'<tr><td class="rk">{i}</td>'
                f'<td>{_html.escape(s.get("name", ""))}</td>'
                f'<td class="{cls}">{sign}{pct:.2f}%</td></tr>')
        body = "".join(rows) or '<tr><td colspan="3" style="color:var(--muted)">—</td></tr>'
        return (f'<div class="sm-col"><div class="sm-hd">{title}</div>'
                f'<table class="sm-tbl">{body}</table></div>')

    ts = _html.escape((movers or {}).get("ts", ""))
    _lnk = "color:var(--accent);font-size:13px;text-decoration:none;margin-left:10px"
    return (
        '<div class="section-hd" style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">'
        '<h2>🇰🇷 한국 업종 등락 TOP 10</h2>'
        f'<a href="theme" style="{_lnk}">🏭 업종별 시세(전체)</a>'
        f'<a href="kr52" style="{_lnk}">📈 신고가·신저가</a>'
        f'<a href="highlow" style="{_lnk}">🚀 급등·급락</a>'
        f'<a href="krprepost" style="{_lnk}">🌙 NXT 급등·급락</a>'
        f'<a href="nxt" style="{_lnk}">📊 NXT 수급</a>'
        f'<span class="ts" style="margin-left:auto">{ts} · Naver</span></div>'
        '<div class="sm-wrap">'
        + _col("🔺 상승 업종", up) + _col("🔻 하락 업종", down)
        + '</div>'
    )


def _render_etf_sector_movers(movers: dict, heading: str, links: str = "") -> str:
    """JP/TW/CN/HK 업종 등락 — 섹터 ETF 합성(Phase 1) 또는 거래소 직접(TWSE,
    Phase 2). KR/US 위젯과 동일 구조(상승/하락 업종). links = 자식 페이지
    링크 HTML(있는 시장만). 데이터 없으면 빈 문자열 → 위젯 자동 생략."""
    up = (movers or {}).get("up", [])
    down = (movers or {}).get("down", [])
    if not up and not down:
        return ""

    def _col(title: str, items: list) -> str:
        rows = []
        for i, s in enumerate(items, 1):
            pct = s.get("pct", 0) or 0
            cls = "up" if pct > 0 else "dn" if pct < 0 else "neu"
            sign = "+" if pct > 0 else ""
            rows.append(
                f'<tr><td class="rk">{i}</td>'
                f'<td>{_html.escape(s.get("name", ""))}</td>'
                f'<td class="{cls}">{sign}{pct:.2f}%</td></tr>')
        body = "".join(rows) or '<tr><td colspan="3" style="color:var(--muted)">—</td></tr>'
        return (f'<div class="sm-col"><div class="sm-hd">{title}</div>'
                f'<table class="sm-tbl">{body}</table></div>')

    ts = _html.escape((movers or {}).get("ts", ""))
    src = _html.escape((movers or {}).get("source", "섹터 ETF·yfinance"))
    return (
        '<div class="section-hd" style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">'
        f'<h2>{heading}</h2>{links}'
        f'<span class="ts" style="margin-left:auto">{ts} · {src}</span></div>'
        '<div class="sm-wrap">'
        + _col("🔺 상승 업종", up) + _col("🔻 하락 업종", down)
        + '</div>'
    )


def _render_us_sector_movers(movers: dict) -> str:
    """🇺🇸 미국 업종 등락 TOP 10 — 메인은 **업종(industry) 단위 상·하위 10**
    (Finviz 144 중, 사용자 2026-06-10 'L3 48개 Top10/Top10 메인'), 세부
    전체 144는 '🏭 업종별 시세'(/usindustry). 52주 신고가·신저가 링크
    (가격제한폭 없는 시장 대응). 데이터 없으면 빈 문자열."""
    up = (movers or {}).get("up", [])
    down = (movers or {}).get("down", [])
    if not up and not down:
        return ""

    def _col(title: str, items: list) -> str:
        rows = []
        for i, s in enumerate(items, 1):
            pct = s.get("pct", 0) or 0
            cls = "up" if pct > 0 else "dn" if pct < 0 else "neu"
            sign = "+" if pct > 0 else ""
            rows.append(
                f'<tr><td class="rk">{i}</td>'
                f'<td>{_html.escape(s.get("name", ""))}</td>'
                f'<td class="{cls}">{sign}{pct:.2f}%</td></tr>')
        body = "".join(rows) or '<tr><td colspan="3" style="color:var(--muted)">—</td></tr>'
        return (f'<div class="sm-col"><div class="sm-hd">{title}</div>'
                f'<table class="sm-tbl">{body}</table></div>')

    ts = _html.escape((movers or {}).get("ts", ""))
    src = _html.escape((movers or {}).get("source", "Finviz"))
    _lnk = "color:var(--accent);font-size:13px;text-decoration:none;margin-left:10px"
    return (
        '<div class="section-hd" style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">'
        '<h2>🇺🇸 미국 업종 등락 TOP 10</h2>'
        f'<a href="usindustry" style="{_lnk}">🏭 업종별 시세(전체)</a>'
        f'<a href="ushighlow" style="{_lnk}">📈 신고가·신저가</a>'
        f'<a href="usmovers" style="{_lnk}">🚀 급등·급락</a>'
        f'<a href="usprepost" style="{_lnk}">🌙 장전·장후</a>'
        f'<span class="ts" style="margin-left:auto">{ts} · {src}</span></div>'
        '<div class="sm-wrap">'
        + _col("🔺 상승 업종", up) + _col("🔻 하락 업종", down)
        + '</div>'
    )


def _render_macro_snapshot(macro: dict) -> str:
    """Full macro snapshot section: cards + 3 charts. Empty string if no data."""
    if not macro:
        return ""
    domestic = macro.get("domestic", [])
    glob = macro.get("global", [])
    charts = macro.get("charts", {})
    if not domestic and not glob:
        return ""

    out: list[str] = []
    out.append(f"""
  <div class="section-hd">
    <h2>Macro Snapshot</h2>
    <span class="ts">{_html.escape(macro.get("ts", ""))} 기준 · 새 데이터 시 하단 알림(30분 체크)</span>
  </div>""")

    if domestic:
        out.append('<div class="macro-sub">국내 지표</div><div class="macro-grid">')
        out.extend(_render_macro_card(i) for i in domestic)
        out.append('</div>')
    if glob:
        out.append('<div class="macro-sub">글로벌 지표</div><div class="macro-grid">')
        out.extend(_render_macro_card(i) for i in glob)
        out.append('</div>')

    # charts row
    cards: list[str] = []
    rf = charts.get("rates_fx")
    if rf:
        _krlbl = rf.get("kr_label", "한국 국고채 10년")
        # 한·미 금리 비교 = 양국 10Y(좌) + 美-韓 금리차(우, 단일 핵심지표). 환율
        # 제외(사용자 2026-06-15 '환율말고 국채 조합'). spread 폴백=직접 계산.
        _us, _kr = rf["us_10y"], rf.get("kr_10y") or rf.get("kr_rate", [])
        _spread = rf.get("spread") or [round(u - k, 2) for u, k in zip(_us, _kr)]
        svg = _svg_line_chart(rf["labels"], [
            {"name": "미국 10Y", "color": "#42a5f5", "data": _us, "axis": "L"},
            {"name": _krlbl, "color": "#26c6da", "data": _kr, "axis": "L"},
            {"name": "美-韓 금리차", "color": "#ab47bc", "data": _spread, "axis": "R"},
        ])
        cards.append(_chart_card(
            "한·미 국채 금리 추이",
            [("미국 10Y (좌)", "#42a5f5"), (f"{_krlbl} (좌)", "#26c6da"),
             ("美-韓 금리차 %p (우)", "#ab47bc")],
            svg, "출처: FRED · 한국은행",
        ))
    inf = charts.get("inflation")
    if inf:
        svg = _svg_line_chart(inf["labels"], [
            {"name": "미국 CPI", "color": "#ffa726", "data": inf["us_cpi"], "axis": "L"},
            {"name": "한국 CPI", "color": "#26a69a", "data": inf["kr_cpi"], "axis": "R"},
        ])
        cards.append(_chart_card(
            "물가와 경기 모멘텀",
            [("미국 CPI (좌)", "#ffa726"), ("한국 CPI (우)", "#26a69a")],
            svg, "출처: FRED · 한국은행",
        ))
    sent = charts.get("sentiment")
    if sent:
        cards.append(_render_sentiment_gauge(sent))
    if cards:
        out.append('<div class="chart-row">' + "".join(cards) + '</div>')

    return "".join(out)


def _load_latest_market_daily(archive_name: str, kind_filter: str = "daily") -> dict | None:
    """Load the most recent archive entry for a market daily surface."""
    archive_dir = Path.home() / ".tradingagents" / archive_name
    if not archive_dir.exists():
        return None
    for d in sorted(archive_dir.iterdir(), reverse=True)[:7]:
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir(), reverse=True):
            if not f.name.endswith(".json"):
                continue
            try:
                rec = json.loads(f.read_text("utf-8"))
                if rec.get("kind", "daily") == kind_filter and rec.get("body"):
                    # daily_byte.html 카드 id 매칭용(딥링크) — 같은 규칙으로 생성.
                    rec["_date"] = d.name
                    rec["_filename"] = f.name
                    return rec
            except Exception:
                continue
    return None


def _extract_daily_title(body: str) -> str:
    """Extract title from Daily Byte body (after 'Daily Byte: YYYYMMDD ')."""
    import re as _re
    first = body.strip().split("\n", 1)[0]
    first = _re.sub(r"<[^>]+>", "", first)
    m = _re.match(r"(?:Daily Byte[:\s]*\d{4}[\.\-]?\d{2}[\.\-]?\d{2}\s*)", first)
    if m:
        return first[m.end():].strip() or first
    if ":" in first and len(first) < 120:
        return first.split(":", 1)[-1].strip() or first
    return first[:80]


def _extract_daily_summary(body: str, max_chars: int = 600) -> str:
    """Extract first substantial paragraph(s) from Daily Byte body (already HTML)."""
    import re as _re_s
    lines = body.strip().split("\n")
    out: list[str] = []
    total = 0
    skip_first = True
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if skip_first:
            skip_first = False
            continue
        plain = _re_s.sub(r"<[^>]+>", "", s)
        # 면책/디스클레이머 줄은 요약에서 제외 (사용자 정책 2026-06-11 —
        # 옛 기록 소급, 새 기록은 _post_process 가 이미 제거).
        if plain.startswith("면책") or (
                "투자 권유" in plain and ("아님" in plain or "아닙" in plain)):
            continue
        if plain.startswith("📊") or plain.startswith("🔥") or plain.startswith("🏆"):
            if total > 100:
                break
        out.append(s)
        total += len(plain)
        if total >= max_chars:
            break
    return "<br>".join(out) if out else ""


def _relative_time(ts_str: str) -> str:
    """Convert ISO timestamp to relative time string (e.g., '3시간 전')."""
    from datetime import datetime as _dt, timezone as _tz
    try:
        ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            from zoneinfo import ZoneInfo
            ts = ts.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        now = _dt.now(tz=ts.tzinfo)
        delta = now - ts
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            return f"{max(mins, 1)}분 전"
        hours = mins // 60
        if hours < 24:
            return f"{hours}시간 전"
        days = hours // 24
        return f"{days}일 전"
    except Exception:
        return ""


def _render_market_daily_cards() -> str:
    """Render the 2-column Market Daily cards (Korea + US → both link to Daily Byte)."""
    kr = _load_latest_market_daily("daily_byte_archive", "daily")
    us = _load_latest_market_daily("daily_byte_archive", "us_daily")

    def _card(rec: dict | None, label: str, flag: str, link: str,
              empty_msg: str, schedule: str) -> str:
        sched = (f'<span class="md-time">⏰ 매일 {_html.escape(schedule)} KST</span>')
        if not rec or not rec.get("body"):
            return (
                f'<div class="md-card">'
                f'<div class="md-label">{flag} {_html.escape(label)} {sched}</div>'
                f'<div class="md-empty">{_html.escape(empty_msg)}</div>'
                f'</div>'
            )
        body = rec["body"]
        title = _extract_daily_title(body)
        # 카드 제목 통일 — 한국 형식 canonical (사용자 2026-06-11): 본문
        # 첫 줄(LLM 가변 — 'U.S. Market Brief' 류) 대신 KR·US 모두 항상
        # 'YYYY년 M월 D일 {한국|미국} 증시 데일리 브리프' 강제. 렌더 시점
        # 보정이라 옛 기록도 즉시 적용. 날짜 파싱 실패 시에만 본문 첫 줄.
        _country = "한국" if label.startswith("한국") else "미국"
        _d_raw = rec.get("_date") or rec.get("date") or ""
        try:
            _y, _m, _dd = str(_d_raw).split("-")
            title = f"{int(_y)}년 {int(_m)}월 {int(_dd)}일 {_country} 증시 데일리 브리프"
        except Exception:
            pass
        summary = _extract_daily_summary(body, 400)
        ts = rec.get("ts", rec.get("date", ""))
        rel = _relative_time(ts) if ts else ""
        rel_span = f' · {_html.escape(rel)}' if rel else ""
        # 딥링크 — daily_byte.html 의 해당 카드 id 로 바로 펼쳐 열림(전체 글).
        # card_id 규칙은 _render_daily_byte_page 와 동일해야 함.
        _d = rec.get("_date", "")
        _fn = rec.get("_filename", "")
        deep = link
        if _d and _fn:
            _cid = f"card-{_d}-{_fn}".replace(".", "_")
            deep = f"{link}#{_cid}"
        return (
            f'<div class="md-card">'
            f'<div class="md-label">{flag} {_html.escape(label)} '
            f'<span class="md-time">⏰ 매일 {_html.escape(schedule)} KST{rel_span}</span></div>'
            f'<h3>{_html.escape(title)}</h3>'
            f'<div class="md-body fade">{summary}</div>'
            f'<a class="md-more" href="{_html.escape(deep)}">전체 보기 →</a>'
            f'</div>'
        )

    kr_card = _card(kr, "한국 Daily Byte", "🇰🇷", "daily_byte.html",
                     "Daily Byte 아카이브가 아직 없습니다.", "19:00")
    us_card = _card(us, "미국 Daily Byte", "🇺🇸", "daily_byte.html",
                     "US Daily Byte 준비 중입니다.", "08:00")
    return f'<div class="md-row">{kr_card}{us_card}</div>'


# 라이브 틱 (사용자 2026-06-15 '홈 위젯 실시간 — 신규기능') — 정적 market.html 은
# 타이머가 30초마다 재생성(no-cache)하므로, 페이지가 스스로 market.html 을 다시
# (구) 30초 #live-sections 자동 swap 은 제거(사용자 2026-07-03 '30분 기준 +
# 팝업으로 내가 결정') — 보던 화면이 바뀌는 강제 갱신 대신 _UPDATE_BANNER_JS
# (30분 HEAD 폴 → 하단 팝업 → 사용자가 새로고침/유지 선택)로 통일. 저사양
# PC 에서 30초마다 전체 페이지 fetch+DOMParser 하던 CPU 비용도 제거(성능 감사).
_MARKET_LIVE_JS = ""


def _render_market_page(data: dict) -> str:
    """Render market.html — global market snapshot + earnings + research."""
    from bot.market_overview import ALL_CARDS

    snap = data.get("snapshot", {})
    yf = snap.get("yf", {})
    fred = snap.get("fred", [])
    dollar_idx = snap.get("dollar_index")
    ts = snap.get("ts", "")
    earnings = data.get("earnings", [])
    research_kr = data.get("research_kr", [])
    research_kr_industry = data.get("research_kr_industry", [])
    research_kr_strategy = data.get("research_kr_strategy", [])
    research_us = data.get("research_us", [])
    # JP/TW/CN/HK 리서치(등급변경) — 데이터 있는 시장만 탭 노출(빈 시장 제거,
    # 사용자 2026-06-13 '보고 판단'). yfinance 해외 커버리지 얇아 희소 가능.
    _res_intl_defs = [(k, lbl, rows) for k, lbl, rows in (
        ("jp", "일본", data.get("research_jp", [])),
        ("tw", "대만", data.get("research_tw", [])),
        ("cn", "중국", data.get("research_cn", [])),
        ("hk", "홍콩", data.get("research_hk", []))) if rows]
    _res_intl_btns = "".join(
        f'<button class="tab-btn" data-tab="res{k}">{lbl}</button>'
        for k, lbl, rows in _res_intl_defs)
    _res_intl_panes = "".join(
        f'<div id="tab-res{k}" class="tab-pane">{_render_research_intl_table(rows)}</div>'
        for k, lbl, rows in _res_intl_defs)
    macro = data.get("macro", {})
    sector_movers = data.get("sector_movers", {})
    us_sector_movers = data.get("us_sector_movers", {})
    deposit = data.get("deposit", {})
    # 위젯별 '실제 적용' 데이터 시각 — 업종등락의 'ts · 소스' 형식을
    # 실적/리서치 헤더에도 (사용자 2026-06-11). ts 부재 시 소스명만.
    _wts = data.get("widget_ts") or {}

    def _src_ts(label: str, ts_key: str, src: str) -> str:
        t = _wts.get(ts_key) or ""
        return f"{label} {t} · {src}" if t else f"{label} {src}"

    _earn_ts = (_src_ts("한국", "earn_kr", "yfinance")
                + " | " + _src_ts("미국", "earn_us", "Finnhub"))
    _res_ts = (_src_ts("한국", "res_kr", "Naver")
               + " | " + _src_ts("미국", "res_us", "yfinance"))

    parts: list[str] = [_MARKET_CSS]
    parts.append(f"""
<div class="wrap">
  <div class="nav">
    <a href="portfolio.html">💼 자산</a>
    &middot; <a href="budget.html">📒 가계부</a>
    &nbsp;|&nbsp;
    <a href="asia.html">🏯 ASIA</a>
    &middot; <a href="index.html">🦉 종목분석</a>
    &middot; <a href="screener.html">📊 Screener</a>
    &middot; <a href="dart_feed.html">📋 DART 공시</a>
    &middot; <a href="valuechain.html">🔗 밸류체인</a>
    &middot; <a href="ppi.html">🏭 PPI</a>
    &middot; <a href="liquidity.html">💧 유동성</a>
    &middot; <a href="trade/">🌏 수출입</a>
    &middot; <a href="daily_byte.html">📰 Daily Byte</a>
    &middot; <a href="reddit_insider.html">📨 미국 레딧</a>
    &middot; <a href="blog.html">📝 블로그</a>
    &nbsp;|&nbsp;
    <a href="realestate.html">🏠 부동산</a>
  </div>
  <h1>🌍 Market Overview</h1>
  <p class="sub">글로벌 시장 현황 · 종목 검색 · 실적 일정 · 리서치 액션 · 업종 등락</p>

  <div class="search-box">
    <input id="mkt-search" type="text"
      placeholder="티커 또는 종목명 검색 (예: NVDA, 005930.KS, 7203.T)"
      autocomplete="off" spellcheck="false">
    <button id="mkt-go" type="button">검색</button>
  </div>

{_render_market_daily_cards()}

  <div id="live-sections">
  <div class="section-hd">
    <h2>글로벌 시장 스냅샷</h2>
    <span class="ts">{_html.escape(ts)} 기준 · 새 데이터 시 하단 알림(30분 체크)</span>
  </div>
  <div class="card-grid">
""")

    for title, items in ALL_CARDS:
        if items is None:
            parts.append(_render_fred_card(fred, dollar_idx))
        else:
            parts.append(_render_market_card(title, items, yf))

    parts.append('</div>')  # close card-grid

    parts.append(_render_macro_snapshot(macro))
    parts.append(_render_deposit_widget(deposit))
    parts.append(_render_deposit_charts(deposit))
    parts.append(_render_sector_movers(sector_movers))
    parts.append(_render_us_sector_movers(us_sector_movers))
    # JP/TW/CN/HK 업종 등락 — 섹터 ETF 합성(Phase 1). TW 는 TWSE 類股(~30) 우선.
    # HK 는 ETF 희소(~4-6)지만 사용자 2026-06-17 요청으로 'Top 10' 라벨 통일(가용분
    # 만큼 표시). 신고저 링크(주요종목 유니버스 스캔).
    # 자식 링크 순서·명칭 통일(사용자 2026-06-13): ① 업종별 시세(전체) ②
    # 신고가·신저가 ③ 상한가·하한가/급등·급락. JP/CN/HK 는 업종-전체 페이지
    # 부재(ETF 합성 위젯 자체) + 가격제한 별도 페이지 부재 → ②만.
    _lk2 = "color:var(--accent);font-size:13px;text-decoration:none;margin-left:10px"
    # 일·중·홍·대(ASIA) 업종 등락은 별도 asia.html 로 분리 — 홈 경량화·성능
    # (사용자 2026-06-15). nav '🏯 ASIA' 링크. 같은 data 로 _render_asia_page 가
    # 렌더(추가 fetch 0). KR·US 는 홈 유지.
    parts.append('</div>')  # close #live-sections

    # 다가오는 실적 — 시장별 탭 분리(사용자 정책: 한국 기본·최대한 표시 +
    # 2026-06-13 '실적빌드 다국가' JP/TW/CN/HK 추가, 접미사 재필터).
    _earn_kr = [e for e in earnings
                if str(e.get("symbol", "")).endswith((".KS", ".KQ"))]
    _earn_jp = [e for e in earnings
                if str(e.get("symbol", "")).endswith(".T")]
    _earn_tw = [e for e in earnings
                if str(e.get("symbol", "")).endswith((".TW", ".TWO"))]
    _earn_cn = [e for e in earnings
                if str(e.get("symbol", "")).endswith((".SS", ".SZ"))]
    _earn_hk = [e for e in earnings
                if str(e.get("symbol", "")).endswith(".HK")]
    _earn_us = [e for e in earnings
                if not str(e.get("symbol", "")).endswith(
                    (".KS", ".KQ", ".T", ".TW", ".TWO", ".SS", ".SZ", ".HK"))]
    # 업커밍 = 가까운 날 먼저(날짜 오름차순) — 종목 알파벳순(소스 기본)이 아니라
    # 금일 기준 가장 임박한 실적이 위로(사용자 2026-06-16 '금일기준 최신이 위로,
    # 미국'). 날짜 동률은 티커, 무날짜는 맨 뒤. 헤더 클릭 재정렬(JS)은 그대로.
    def _earn_by_date(rows: list) -> list:
        return sorted(rows, key=lambda e: (str(e.get("date") or "9999-99-99"),
                                           str(e.get("symbol") or "")))
    _earn_kr = _earn_by_date(_earn_kr)
    _earn_us = _earn_by_date(_earn_us)
    _earn_jp = _earn_by_date(_earn_jp)
    _earn_tw = _earn_by_date(_earn_tw)
    _earn_cn = _earn_by_date(_earn_cn)
    _earn_hk = _earn_by_date(_earn_hk)
    # 미국 종목 한글명 ( ) — 신고저 대시보드와 동일 소스(Naver koreanCodeName,
    # 7d 캐시). 실적·리서치 US 행에 주입 → '티커 (한글명)' 표시(사용자 2026-06-15
    # '미국도 다른나라처럼 한글명'). 미매핑(소형주)은 티커만. 1회 fetch·graceful.
    # ⚠️ 반드시 _etab_panes(실적표) 빌드 **전**에 — 표 렌더가 name 을 읽으므로.
    try:
        from bot.naver_ranking_client import world_upjong_name as _wun
        _us_kr = _wun("US") or {}
    except Exception:
        _us_kr = {}
    for _r in _earn_us:
        _r["name"] = _us_kr.get(str(_r.get("symbol", "")), "")
    for _r in research_us:
        _r["name_kr"] = _us_kr.get(str(_r.get("symbol", "")), "")
    # 빈 시장 탭 제거 (사용자 2026-06-13 '내용없으면 지워' — 중국 0건 등). 한국
    # 우선 순서 유지, 첫 비어있지 않은 탭이 active. 모두 비면 안내 문구.
    # HK 를 TW 앞으로 (사용자 2026-06-14 '대만·홍콩 순서 바꿔' — market.html 위젯
    # 순서와 통일).
    _etab_defs = [(k, lbl, rows) for k, lbl, rows in (
        ("kr", "한국", _earn_kr), ("us", "미국", _earn_us),
        ("jp", "일본", _earn_jp), ("hk", "홍콩", _earn_hk),
        ("cn", "중국", _earn_cn), ("tw", "대만", _earn_tw)) if rows]
    if _etab_defs:
        _etab_btns = "".join(
            f'<button class="etab-btn{" active" if i == 0 else ""}" '
            f'data-etab="{k}">{lbl} ({len(rows)})</button>'
            for i, (k, lbl, rows) in enumerate(_etab_defs))
        _etab_panes = "".join(
            f'<div id="etab-{k}" class="etab-pane{" active" if i == 0 else ""}">'
            f'{_render_earnings_table(rows)}</div>'
            for i, (k, lbl, rows) in enumerate(_etab_defs))
    else:
        _etab_btns = ""
        _etab_panes = ('<div class="etab-pane active">'
                       '<div class="empty-msg">실적 발표 일정이 없습니다.</div></div>')

    # 날짜 드롭다운 옵션 (사용자 2026-06-15 '날짜 입력 말고 스크롤 내려서 선택').
    # 실적=업커밍 → 오름차순(가까운 날 먼저), 리서치=과거 → 내림차순(최근 먼저).
    # 빈 '전체 날짜' = 필터 해제. wireDateFilter 가 from~to 범위로 행 필터.
    def _date_opts(dates: list) -> str:
        return ('<option value="">전체 날짜</option>'
                + "".join(f'<option value="{_html.escape(d)}">{_html.escape(d)}</option>'
                          for d in dates))
    _eopts = _date_opts(sorted({str(e.get("date")) for e in earnings if e.get("date")}))
    _res_rows_all = (research_kr + research_kr_industry + research_kr_strategy
                     + research_us
                     + [r for _k, _l, _rows in _res_intl_defs for r in _rows])
    _ropts = _date_opts(sorted({str(r.get("date")) for r in _res_rows_all
                                if r.get("date")}, reverse=True))

    parts.append(f"""
  <div class="section-hd" style="display:flex;align-items:baseline;gap:6px;flex-wrap:wrap">
    <h2>다가오는 실적</h2>
    <a href="earnings" style="color:var(--accent);font-size:13px;text-decoration:none;margin-left:10px">📅 실적 캘린더(한국 IR·미국·일·홍·대 실적)</a>
    <span class="ts" style="margin-left:auto">{_html.escape(_earn_ts)}</span>
  </div>
  <div class="tbl-filter">
    <input id="earn-filter" type="text" placeholder="종목 검색 (AAPL, NVDA …)" autocomplete="off">
    <select id="earn-from" class="dfilt" title="시작일(이 날짜부터)">{_eopts}</select>
    <span class="dsep">~</span>
    <select id="earn-to" class="dfilt" title="종료일(이 날짜까지)">{_eopts}</select>
    <span class="cnt" id="earn-cnt"></span>
    <span class="cnt" style="margin-left:auto">날짜 드롭다운·종목 필터 · ↕ 헤더 클릭 정렬</span>
  </div>
  <div class="tabs">{_etab_btns}</div>
  {_etab_panes}

  <div class="section-hd" style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
    <h2>최근 리서치 액션</h2>
    <span class="ts" style="margin-left:auto">{_html.escape(_res_ts)}</span>
  </div>
  <div class="tbl-filter">
    <input id="research-filter" type="text" placeholder="산업·증권사·제목 검색 …" autocomplete="off">
    <select id="research-from" class="dfilt" title="시작일(이 날짜부터)">{_ropts}</select>
    <span class="dsep">~</span>
    <select id="research-to" class="dfilt" title="종료일(이 날짜까지)">{_ropts}</select>
    <span class="cnt" id="research-cnt"></span>
    <span class="cnt" style="margin-left:auto">날짜 드롭다운·검색 필터 · ↕ 헤더 클릭 정렬</span>
  </div>
  <div class="tabs">
    <button class="tab-btn active" data-tab="kr">한국 기업</button>
    <button class="tab-btn" data-tab="krind">한국 산업</button>
    <button class="tab-btn" data-tab="krstrat">한국 전략</button>
    <button class="tab-btn" data-tab="us">미국</button>
    {_res_intl_btns}
  </div>
  <div id="tab-kr" class="tab-pane active">
    {_render_research_kr_table(research_kr)}
  </div>
  <div id="tab-krind" class="tab-pane">
    {_render_research_industry_table(research_kr_industry)}
  </div>
  <div id="tab-krstrat" class="tab-pane">
    {_render_research_strategy_table(research_kr_strategy)}
  </div>
  <div id="tab-us" class="tab-pane">
    {_render_research_us_table(research_us)}
  </div>
  {_res_intl_panes}

  <div id="fav-section">
    <div class="fav-hd"><h2>⭐ 관심종목</h2><span class="cnt" id="fav-cnt"></span></div>
    <div id="fav-body"><div class="md-empty">불러오는 중…</div></div>
  </div>
</div>

<script>
(function() {{
  /* 홈 복귀 시 마지막 위치 복원 (사용자 2026-06-15 '미국 보이던 위치로, 새로고침
     맨 위 아니라'). 어떤 경로로 홈(market.html)에 도착하든 — 자식 대시보드 홈
     버튼·full reload — 마지막 스크롤로 복귀. history.back() 가로채기 아님.
     ⚠️ 관심종목 등 async 콘텐츠가 높이를 늦게 잡으므로 0~2s 여러 번 재적용
     (한 번이면 콘텐츠 로딩 전이라 맨 위로 튐). 사용자가 휠/터치/키로 직접
     움직이면 즉시 중단(복원이 사용자 스크롤과 싸우지 않게). */
  try {{
    if (history.scrollRestoration) history.scrollRestoration = 'manual';
    var _SK = 'noah_home_scroll';
    var _sy = sessionStorage.getItem(_SK);
    var _target = (_sy !== null) ? (parseInt(_sy) || 0) : null;
    var _settled = (_target === null), _userActed = false;
    ['wheel', 'touchstart', 'keydown'].forEach(function(ev) {{
      window.addEventListener(ev, function() {{ _userActed = true; _settled = true; }},
                              {{passive: true, once: true}});
    }});
    if (_target !== null) {{
      var _r = function() {{ if (!_userActed) window.scrollTo(0, _target); }};
      _r();
      [120, 350, 700, 1200, 2000].forEach(function(d) {{ setTimeout(_r, d); }});
      window.addEventListener('load', _r);
      setTimeout(function() {{ _settled = true; }}, 2100);
    }}
    window.addEventListener('scroll', function() {{
      if (_settled) {{ try {{ sessionStorage.setItem(_SK, String(window.scrollY)); }} catch (_e) {{}} }}
    }}, {{passive: true}});
  }} catch (_e) {{}}

  var inp = document.getElementById('mkt-search');
  var btn = document.getElementById('mkt-go');
  function go() {{
    var q = (inp.value || '').trim();
    if (!q) return;
    btn.disabled = true; btn.textContent = '…';
    fetch('api/search?q=' + encodeURIComponent(q))
      .then(function(r){{ return r.json(); }})
      .then(function(d) {{
        if (d.ticker) {{
          window.location.href = 'lookup/' + encodeURIComponent(d.ticker);
        }} else {{
          window.location.href = 'lookup/' + encodeURIComponent(q);
        }}
      }})
      .catch(function() {{
        window.location.href = 'lookup/' + encodeURIComponent(q);
      }});
  }}
  btn.addEventListener('click', go);
  inp.addEventListener('keydown', function(e) {{
    if (e.key === 'Enter') go();
  }});
  inp.focus();

  /* tab switching (리서치 액션) */
  document.querySelectorAll('.tab-btn').forEach(function(b) {{
    b.addEventListener('click', function() {{
      document.querySelectorAll('.tab-btn').forEach(function(x) {{ x.classList.remove('active'); }});
      document.querySelectorAll('.tab-pane').forEach(function(x) {{ x.classList.remove('active'); }});
      b.classList.add('active');
      document.getElementById('tab-' + b.dataset.tab).classList.add('active');
    }});
  }});

  /* tab switching (다가오는 실적 한국/미국 — 독립 그룹) */
  document.querySelectorAll('.etab-btn').forEach(function(b) {{
    b.addEventListener('click', function() {{
      document.querySelectorAll('.etab-btn').forEach(function(x) {{ x.classList.remove('active'); }});
      document.querySelectorAll('.etab-pane').forEach(function(x) {{ x.classList.remove('active'); }});
      b.classList.add('active');
      document.getElementById('etab-' + b.dataset.etab).classList.add('active');
    }});
  }});

  /* show-more / 접기 toggle. applyLimit 는 wrap 에 노출 → 정렬 후 재적용. */
  document.querySelectorAll('[data-limit]').forEach(function(wrap) {{
    var limit = parseInt(wrap.dataset.limit);
    var tbl = wrap.querySelector('.dtbl');
    if (!tbl) return;
    wrap._expanded = false;
    /* 현재 DOM 순서로 limit 재적용(정렬·더보기 공용). 펼친 상태면 전부 표시. */
    function applyLimit() {{
      var rs = tbl.querySelectorAll('tbody tr');
      for (var i = 0; i < rs.length; i++) {{
        if (wrap._expanded || i < limit) {{
          rs[i].classList.remove('xrow');
          rs[i].style.display = '';
        }} else {{
          rs[i].classList.add('xrow');
          rs[i].style.display = 'none';
        }}
      }}
    }}
    wrap._applyLimit = applyLimit;
    if (tbl.querySelectorAll('tbody tr').length <= limit) return;
    var extra = tbl.querySelectorAll('tbody tr').length - limit;
    applyLimit();
    var btn = document.createElement('button');
    btn.className = 'show-more-btn';
    btn.textContent = '더 보기 (' + extra + '개 더)';
    btn.addEventListener('click', function() {{
      wrap._expanded = !wrap._expanded;
      applyLimit();
      btn.textContent = wrap._expanded ? '접기' : ('더 보기 (' + extra + '개 더)');
    }});
    wrap.insertAdjacentElement('afterend', btn);
  }});

  /* 정렬 — dtbl th 클릭(오름/내림 토글). 숫자(통화/%/조억/B/M)·날짜·문자
     자동 판별. 정렬 후 wrap._applyLimit 로 더보기 상태 보존. (사용자
     2026-06-10 리서치·실적 정렬 필터.) */
  document.querySelectorAll('.dtbl').forEach(function(tbl) {{
    if (!tbl.tHead || !tbl.tHead.rows.length) return;
    var ths = tbl.tHead.rows[0].cells;
    var dir = {{}};
    function num(s) {{
      var m = s.replace(/[,%₩$\\s]/g, '').replace(/조/g, 'e12').replace(/억/g, 'e8')
               .replace(/([0-9.])B$/, '$1e9').replace(/([0-9.])M$/, '$1e6');
      if (!/^[+-]?\\d*\\.?\\d+(e[+-]?\\d+)?$/.test(m)) return null;
      return parseFloat(m);
    }}
    Array.prototype.forEach.call(ths, function(th, idx) {{
      th.addEventListener('click', function() {{
        var tb = tbl.tBodies[0]; if (!tb) return;
        var d = dir[idx] = -(dir[idx] || 1);
        var rows = [].slice.call(tb.rows);
        function cellVal(r) {{ var c = r.cells[idx]; return c ? (c.textContent || '').trim() : ''; }}
        var allNum = rows.length > 0 && rows.every(function(r) {{
          var v = cellVal(r); return v === '' || v === '—' || num(v) !== null; }});
        rows.sort(function(a, b) {{
          var av = cellVal(a), bv = cellVal(b);
          if (allNum) {{
            var an = num(av), bn = num(bv);
            an = (an === null) ? -Infinity : an;
            bn = (bn === null) ? -Infinity : bn;
            return d * (an - bn);
          }}
          return d * av.localeCompare(bv, 'ko');
        }});
        rows.forEach(function(r) {{ tb.appendChild(r); }});
        Array.prototype.forEach.call(ths, function(h) {{ h.classList.remove('sort-asc', 'sort-desc'); }});
        th.classList.add(d > 0 ? 'sort-asc' : 'sort-desc');
        var wrap = tbl.closest('[data-limit]');
        if (wrap && wrap._applyLimit) wrap._applyLimit();
      }});
    }});
  }});

  /* table filter helper */
  function wireFilter(inputId, cntId, tableParentId) {{
    var fi = document.getElementById(inputId);
    var cn = document.getElementById(cntId);
    if (!fi) return;
    var parent = tableParentId ? document.getElementById(tableParentId) : fi.parentElement.nextElementSibling;
    fi.addEventListener('input', function() {{
      var q = (fi.value || '').trim().toLowerCase();
      var tables = (parent ? [parent] : []).concat(
        Array.from(document.querySelectorAll(tableParentId ? '#' + tableParentId + ' .dtbl' : '.dtbl'))
      );
      var total = 0, shown = 0;
      tables.forEach(function(wrap) {{
        var tbl = wrap.tagName === 'TABLE' ? wrap : wrap.querySelector('.dtbl');
        if (!tbl) return;
        tbl.querySelectorAll('tbody tr').forEach(function(row) {{
          total++;
          var txt = row.textContent.toLowerCase();
          var vis = !q || txt.indexOf(q) >= 0;
          if (vis && !q && row.classList.contains('xrow')) vis = false;
          row.style.display = vis ? '' : 'none';
          if (vis) shown++;
        }});
      }});
      cn.textContent = q ? shown + '/' + total : '';
    }});
  }}
  /* 텍스트 + 날짜범위 결합 필터 (다가오는 실적·리서치, 사용자 2026-06-15
     '정렬말고 필터로 — 7~20일꺼만, 그날 누가 발표하는지'). 행에서 ISO 날짜
     (YYYY-MM-DD / YYYY.MM.DD) 자동 추출 → from~to 범위 + 텍스트 동시 적용.
     날짜 필터 활성 시 날짜 없는 행은 숨김(범위 밖). 프리셋이 from/to 채움. */
  function wireDateFilter(textId, fromId, toId, cntId, rowSel) {{
    var ti = document.getElementById(textId), fi = document.getElementById(fromId),
        to = document.getElementById(toId), cn = document.getElementById(cntId);
    if (!ti && !fi) return;
    function rowDate(row) {{
      var m = row.textContent.match(/(\\d{{4}})[-.](\\d{{2}})[-.](\\d{{2}})/);
      return m ? (m[1] + '-' + m[2] + '-' + m[3]) : '';
    }}
    function apply() {{
      var q = ((ti && ti.value) || '').trim().toLowerCase();
      var df = (fi && fi.value) || '', dt = (to && to.value) || '';
      var hasDate = !!(df || dt), active = !!(q || hasDate);
      var total = 0, shown = 0;
      document.querySelectorAll(rowSel).forEach(function(row) {{
        total++;
        var tp = !q || row.textContent.toLowerCase().indexOf(q) >= 0;
        var dp = true;
        if (hasDate) {{
          var rd = rowDate(row);
          dp = !!rd && (!df || rd >= df) && (!dt || rd <= dt);
        }}
        var vis = tp && dp;
        if (vis && !active && row.classList.contains('xrow')) vis = false;
        row.style.display = vis ? '' : 'none';
        if (vis) shown++;
      }});
      if (cn) cn.textContent = active ? shown + '/' + total : '';
    }}
    if (ti) ti.addEventListener('input', apply);
    if (fi) fi.addEventListener('change', apply);
    if (to) to.addEventListener('change', apply);
    var bar = (ti || fi).closest('.tbl-filter');
    if (bar) bar.querySelectorAll('.dpreset').forEach(function(b) {{
      b.addEventListener('click', function() {{
        bar.querySelectorAll('.dpreset').forEach(function(o) {{ o.classList.remove('on'); }});
        if (b.dataset.clear) {{ if (fi) fi.value = ''; if (to) to.value = ''; }}
        else {{
          var base = new Date(); base.setHours(0, 0, 0, 0);
          function iso(off) {{ var x = new Date(base); x.setDate(x.getDate() + (+off));
            return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0')
                 + '-' + String(x.getDate()).padStart(2, '0'); }}
          if (fi) fi.value = iso(b.dataset.from); if (to) to.value = iso(b.dataset.to);
          b.classList.add('on');
        }}
        apply();
      }});
    }});
  }}
  wireDateFilter('earn-filter', 'earn-from', 'earn-to', 'earn-cnt', '.etab-pane .dtbl tbody tr');
  wireDateFilter('research-filter', 'research-from', 'research-to', 'research-cnt', '.tab-pane .dtbl tbody tr');

  /* ── Favorites CRUD ── */
  (function() {{
    var favBody = document.getElementById('fav-body');
    var favCnt = document.getElementById('fav-cnt');
    var favState = {{ sortK: null, sortDir: 1, country: 'ALL', earnFrom: '', earnTo: '', page: 0, showAll: false }};
    var FAV_PAGE_SIZE = 10;   /* 한 화면 10개, 그 이상은 페이지 번호로 (사용자 2026-06-16) */

    var FLAG = {{'US':'🇺🇸','KR':'🇰🇷','JP':'🇯🇵','TW':'🇹🇼','CN':'🇨🇳','HK':'🇭🇰','UK':'🇬🇧','DE':'🇩🇪','FR':'🇫🇷'}};
    /* 정렬 전용 USD 환산율 (사용자 2026-06-13 '통화기호 달라도 통합
       시총순') — 표시는 원통화 그대로, data-* 정렬키만 ÷FX. 정적 근사
       (usage_tracker 1380 기준) — 순서 판정 목적이라 충분. */
    var FX = {{'KRW':1380,'USD':1,'JPY':155,'TWD':32,'HKD':7.8,'EUR':0.92,'GBP':0.79,'CNY':7.2}};

    function fmtPrice(v, sym) {{
      if (v == null) return '—';
      sym = sym || '$';
      return sym + Number(v).toLocaleString(undefined, {{minimumFractionDigits:0, maximumFractionDigits:2}});
    }}
    function fyTag(label) {{
      if (!label) return '';
      return ' <span style="font-size:10px;color:var(--muted);white-space:nowrap">(' + label + ')</span>';
    }}

    function fmtEstLabel(v, isActual, sym, fyLabel) {{
      if (v == null) return '—';
      sym = sym || '';
      var num = (typeof v === 'number')
        ? Number(v).toLocaleString(undefined, {{maximumFractionDigits:2}})
        : String(v);
      return sym + num + (isActual ? fyTag(fyLabel) : '');
    }}

    function fmtPER(v, isTrailing, fyLabel, epsNeg) {{
      if (epsNeg) return '<span style="color:var(--muted)">적자</span>';
      if (v == null) return '—';
      if (v < 0) return '<span style="color:var(--muted)">적자</span>';
      return Number(v).toFixed(1) + (isTrailing ? fyTag(fyLabel) : '');
    }}

    function fmtMcap(v, sym) {{
      if (v == null) return '—';
      sym = sym || '$';
      if (sym === '₩' || sym === '¥') {{
        if (v >= 1e12) return sym + (v/1e12).toFixed(1) + '조';
        if (v >= 1e8) return sym + Math.round(v/1e8).toLocaleString() + '억';
        return sym + Number(v).toLocaleString();
      }}
      if (v >= 1e12) return sym + (v/1e12).toFixed(2) + 'T';
      if (v >= 1e9) return sym + (v/1e9).toFixed(1) + 'B';
      if (v >= 1e6) return sym + (v/1e6).toFixed(0) + 'M';
      return sym + Number(v).toLocaleString();
    }}

    function arrow(k) {{
      return (favState.sortK === k) ? (favState.sortDir > 0 ? ' ▲' : ' ▼') : '';
    }}

    function renderFavs(list) {{
      favState.sortK = null;  /* 새 데이터 = 저장 순서로 표시 */
      favState.page = 0;      /* 새 데이터 = 1페이지부터 (showAll 은 유지) */
      favCnt.textContent = list.length ? list.length + '종목' : '';
      if (!list.length) {{
        favBody.innerHTML = '<div class="md-empty">종목 검색 후 상세 페이지에서 ⭐ 저장 버튼을 눌러주세요.</div>';
        return;
      }}

      /* 나라 필터 옵션 (존재하는 나라만) */
      var countries = [];
      list.forEach(function(f) {{ if (f.country && countries.indexOf(f.country) < 0) countries.push(f.country); }});
      if (favState.country !== 'ALL' && countries.indexOf(favState.country) < 0) favState.country = 'ALL';
      var copts = '<option value="ALL">나라</option>';
      countries.forEach(function(c) {{
        copts += '<option value="' + c + '"' + (favState.country === c ? ' selected' : '') + '>' + (FLAG[c] || c) + '</option>';
      }});
      /* 다음예상 실적일 드롭다운 (사용자 2026-06-15 '관심종목도 날짜 필터') —
         가용 실적일만 스크롤 선택, from~to 범위. 선택 보존(재렌더). */
      var edates = [];
      list.forEach(function(f) {{ if (f.next_earnings && edates.indexOf(f.next_earnings) < 0) edates.push(f.next_earnings); }});
      edates.sort();
      function dopts(sel) {{
        var o = '<option value="">전체 실적일</option>';
        edates.forEach(function(d) {{ o += '<option value="' + d + '"' + (sel === d ? ' selected' : '') + '>' + d + '</option>'; }});
        return o;
      }}
      if (favState.earnFrom && edates.indexOf(favState.earnFrom) < 0) favState.earnFrom = '';
      if (favState.earnTo && edates.indexOf(favState.earnTo) < 0) favState.earnTo = '';
      var dctrl = edates.length
        ? ('<select id="fav-earn-from" class="dfilt" title="실적일 시작">' + dopts(favState.earnFrom) + '</select>'
           + '<span class="dsep">~</span>'
           + '<select id="fav-earn-to" class="dfilt" title="실적일 종료">' + dopts(favState.earnTo) + '</select>')
        : '';
      var ctrl = '<div class="fav-ctrl">'
        + '<select id="fav-country">' + copts + '</select>' + dctrl
        + '<span style="font-size:11px;color:var(--muted)">↕ 화살표로 순서 변경 · 헤더 클릭 정렬</span></div>';

      /* 정렬 헤더 (data-k/data-t) */
      var cols = [['name','s','종목'],['country','s','나라'],['saved','s','저장일'],
        ['mcap','n','시총'],['sprice','n','저장가격'],['cprice','n','현재가격'],
        ['pct','n','저장대비'],['eps','n','예상 EPS'],['per','n','예상 PER'],['earn','s','다음예상 실적일']];
      var thead = '<tr>';
      cols.forEach(function(c, i) {{
        var al = (i === 0) ? ' style="text-align:left"' : '';
        thead += '<th class="fav-sort" data-k="' + c[0] + '" data-t="' + c[1] + '"' + al + '>'
          + c[2] + '<span class="fav-ar">' + arrow(c[0]) + '</span></th>';
      }});
      thead += '<th>순서</th><th></th></tr>';

      var rows = list.map(function(f) {{
        var flag = FLAG[f.country] || '';
        var curCell = '—', pctCell = '—', pctVal = '';
        if (f.current_price != null) {{
          curCell = fmtPrice(f.current_price, f.currency_symbol);
          if (f.saved_price != null && f.saved_price > 0) {{
            var pct = (f.current_price - f.saved_price) / f.saved_price * 100;
            pctVal = pct;
            var clr = pct > 0 ? '#059669' : pct < 0 ? '#dc2626' : 'inherit';
            var sign = pct > 0 ? '+' : '';
            pctCell = '<span style="color:' + clr + '">' + sign + pct.toFixed(1) + '%</span>';
          }}
        }}
        /* 통화 통합 정렬키 (USD 환산) — ₩1526조 가 $1.13T 위로 가게 */
        var fx = FX[f.currency] || 1;
        function usd(v) {{ return v != null ? (v / fx) : ''; }}
        var da = 'data-name="' + ((f.name_kr||'') + ' ' + (f.name||'') + ' ' + f.ticker).toLowerCase() + '" data-country="' + (f.country || '') + '"'
          + ' data-saved="' + (f.saved_date || '') + '" data-mcap="' + usd(f.market_cap) + '"'
          + ' data-sprice="' + usd(f.saved_price) + '" data-cprice="' + usd(f.current_price) + '"'
          + ' data-pct="' + (pctVal !== '' ? pctVal : '') + '" data-eps="' + usd(f.eps_estimate) + '"'
          + ' data-per="' + (f.per != null ? f.per : '') + '" data-earn="' + (f.next_earnings || '') + '"';
        return '<tr ' + da + '>'
          + '<td style="text-align:left"><a href="lookup/' + encodeURIComponent(f.ticker) + '" style="color:inherit;text-decoration:none">'
          + '<span style="font-weight:600;font-size:13px">' + (f.name_kr||f.name||f.ticker) + '</span>'
          + '<span style="display:block;font-size:11px;color:var(--muted);font-weight:400;margin-top:1px">' + f.ticker + '</span></a></td>'
          + '<td>' + flag + '</td>'
          + '<td style="white-space:nowrap">' + (f.saved_date||'') + '</td>'
          + '<td style="white-space:nowrap">' + fmtMcap(f.market_cap, f.currency_symbol) + '</td>'
          + '<td style="white-space:nowrap">' + fmtPrice(f.saved_price, f.currency_symbol) + '</td>'
          + '<td style="white-space:nowrap">' + curCell + '</td>'
          + '<td style="white-space:nowrap">' + pctCell + '</td>'
          + '<td style="white-space:nowrap">' + fmtEstLabel(f.eps_estimate, f.eps_is_actual, f.currency_symbol, f.eps_fy_label) + '</td>'
          + '<td style="white-space:nowrap">' + fmtPER(f.per, f.per_is_trailing, f.eps_fy_label, f.eps_negative) + '</td>'
          + '<td style="white-space:nowrap">' + (f.next_earnings||'—') + '</td>'
          + '<td class="fav-ord"><button class="fav-top" data-ticker="' + f.ticker + '" title="맨 위로">⤒</button>'
          + '<button class="fav-up" data-ticker="' + f.ticker + '" title="위로">▲</button>'
          + '<button class="fav-down" data-ticker="' + f.ticker + '" title="아래로">▼</button>'
          + '<button class="fav-bottom" data-ticker="' + f.ticker + '" title="맨 아래로">⤓</button></td>'
          + '<td><button class="fav-del" data-ticker="' + f.ticker + '" title="삭제">✕</button></td>'
          + '</tr>';
      }}).join('');

      favBody.innerHTML = ctrl + '<table class="dtbl" id="fav-tbl"><thead>' + thead + '</thead><tbody>' + rows + '</tbody></table>'
        + '<div id="fav-pager" class="fav-pager"></div>';

      favBody.querySelectorAll('.fav-del').forEach(function(b) {{
        b.addEventListener('click', function() {{ removeFav(b.dataset.ticker); }});
      }});
      favBody.querySelectorAll('.fav-up').forEach(function(b) {{
        b.addEventListener('click', function() {{ reorderFav(b.dataset.ticker, 'up'); }});
      }});
      favBody.querySelectorAll('.fav-down').forEach(function(b) {{
        b.addEventListener('click', function() {{ reorderFav(b.dataset.ticker, 'down'); }});
      }});
      favBody.querySelectorAll('.fav-top').forEach(function(b) {{
        b.addEventListener('click', function() {{ reorderFav(b.dataset.ticker, 'top'); }});
      }});
      favBody.querySelectorAll('.fav-bottom').forEach(function(b) {{
        b.addEventListener('click', function() {{ reorderFav(b.dataset.ticker, 'bottom'); }});
      }});
      var csel = document.getElementById('fav-country');
      if (csel) csel.addEventListener('change', function() {{ favState.country = csel.value; favState.page = 0; applyFavFilter(); }});
      var efr = document.getElementById('fav-earn-from');
      if (efr) efr.addEventListener('change', function() {{ favState.earnFrom = efr.value; favState.page = 0; applyFavFilter(); }});
      var eto = document.getElementById('fav-earn-to');
      if (eto) eto.addEventListener('change', function() {{ favState.earnTo = eto.value; favState.page = 0; applyFavFilter(); }});
      var tbl = document.getElementById('fav-tbl');
      [].forEach.call(tbl.tHead.rows[0].cells, function(th) {{
        if (th.dataset.k) th.addEventListener('click', function() {{ sortFav(th.dataset.k, th.dataset.t); }});
      }});
      applyFavFilter();
    }}

    function applyFavFilter() {{
      var tbl = document.getElementById('fav-tbl');
      if (!tbl) return;
      var total = 0, pass = [];
      var df = favState.earnFrom || '', dt = favState.earnTo || '';
      var dateOn = !!(df || dt);
      /* 1차: 나라·실적일 필터 통과 행을 현재(정렬) 순서로 수집 */
      [].forEach.call(tbl.tBodies[0].rows, function(tr) {{
        total++;
        var ok = (favState.country === 'ALL') || (tr.dataset.country === favState.country);
        if (ok && dateOn) {{   /* 다음예상 실적일(data-earn) 범위 필터(사용자 2026-06-15) */
          var ed = tr.dataset.earn || '';
          ok = !!ed && (!df || ed >= df) && (!dt || ed <= dt);
        }}
        tr.style.display = 'none';   /* 일단 전부 숨김 → 아래서 현재 페이지만 노출 */
        if (ok) pass.push(tr);
      }});
      /* 2차: 페이지네이션 — 통과 행 중 현재 페이지 10개만 노출(전체 펼치기면 전부) */
      var shown = pass.length;
      var pages = Math.max(1, Math.ceil(shown / FAV_PAGE_SIZE));
      if (favState.page >= pages) favState.page = pages - 1;
      if (favState.page < 0) favState.page = 0;
      var start = favState.showAll ? 0 : favState.page * FAV_PAGE_SIZE;
      var end = favState.showAll ? shown : (start + FAV_PAGE_SIZE);
      pass.slice(start, end).forEach(function(tr) {{ tr.style.display = ''; }});
      favCnt.textContent = (favState.country === 'ALL' && !dateOn) ? (total + '종목') : (shown + '/' + total + '종목');
      renderFavPager(shown, pages);
    }}

    function renderFavPager(shown, pages) {{
      var el = document.getElementById('fav-pager');
      if (!el) return;
      if (shown <= FAV_PAGE_SIZE) {{ el.innerHTML = ''; return; }}   /* 10개 이하 = nav 불필요 */
      var h = '';
      if (favState.showAll) {{
        h = '<button class="fav-pg" data-pg="collapse">↩ 10개씩 보기</button>';
      }} else {{
        for (var i = 0; i < pages; i++) {{
          h += '<button class="fav-pg' + (i === favState.page ? ' active' : '') + '" data-pg="' + i + '">' + (i + 1) + '</button>';
        }}
        h += '<button class="fav-pg" data-pg="all">전체 펼치기 (' + shown + ')</button>';
      }}
      el.innerHTML = h;
      el.querySelectorAll('.fav-pg').forEach(function(b) {{
        b.addEventListener('click', function() {{
          var v = b.dataset.pg;
          if (v === 'all') favState.showAll = true;
          else if (v === 'collapse') {{ favState.showAll = false; favState.page = 0; }}
          else favState.page = parseInt(v, 10);
          applyFavFilter();
        }});
      }});
    }}

    function sortFav(k, t) {{
      var tbl = document.getElementById('fav-tbl');
      if (!tbl) return;
      if (favState.sortK === k) favState.sortDir = -favState.sortDir;
      else {{ favState.sortK = k; favState.sortDir = (t === 'n' ? -1 : 1); }}
      favState.page = 0;   /* 정렬 바뀌면 1페이지부터 */
      var tb = tbl.tBodies[0];
      [].slice.call(tb.rows).sort(function(a, b) {{
        var av = a.dataset[k], bv = b.dataset[k];
        if (t === 'n') {{
          var an = parseFloat(av), bn = parseFloat(bv);
          if (isNaN(an) && isNaN(bn)) return 0;
          if (isNaN(an)) return 1;
          if (isNaN(bn)) return -1;
          return (an - bn) * favState.sortDir;
        }}
        return String(av).localeCompare(String(bv), 'ko') * favState.sortDir;
      }}).forEach(function(tr) {{ tb.appendChild(tr); }});
      [].forEach.call(tbl.tHead.rows[0].cells, function(th) {{
        var ar = th.querySelector('.fav-ar');
        if (ar) ar.textContent = (th.dataset.k === k) ? (favState.sortDir > 0 ? ' ▲' : ' ▼') : '';
      }});
      applyFavFilter();   /* 새 순서로 페이지네이션 재적용 */
    }}

    function loadFavs() {{
      fetch('api/favorites')
        .then(function(r) {{ return r.json(); }})
        .then(function(d) {{ renderFavs(d.favorites || []); }})
        .catch(function() {{ favBody.innerHTML = '<div class="md-empty">관심종목을 불러올 수 없습니다.</div>'; }});
    }}

    function removeFav(ticker) {{
      fetch('api/favorite_remove', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ticker: ticker}})
      }})
        .then(function() {{ loadFavs(); }})
        .catch(function() {{ alert('삭제 실패'); }});
    }}

    function reorderFav(ticker, direction) {{
      fetch('api/favorite_reorder', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ticker: ticker, direction: direction}})
      }})
        .then(function() {{ loadFavs(); }})
        .catch(function() {{ alert('순서 변경 실패'); }});
    }}


    loadFavs();
    // SWR: 엔드포인트가 콜드 시 이름만 즉시 주고 백그라운드로 가격 채움(사용자
    // 2026-06-16 '오래걸려' fix) → 5초 뒤 1회 재폴(가격 픽업) + 60초 라이브 폴.
    setTimeout(loadFavs, 5000);
    setInterval(function() {{ if (!document.hidden) loadFavs(); }}, 60000);
  }})();
}})();
</script>
{_MARKET_LIVE_JS}
</body></html>
""")
    return "".join(parts)


# ASIA 별도 대시보드 (일·중·홍·대 업종 등락을 홈에서 분리 — 홈 경량화·성능,
# 사용자 2026-06-15). (구) 30초 라이브 swap 은 제거(사용자 2026-07-03 '30분
# 기준 + 팝업으로 내가 결정') — _UPDATE_BANNER_JS 로 통일. 진입 위치 복원만 유지.
# asia.html 은 regenerate_market_index 가 홈과 같은 data 로 함께 렌더(추가 fetch 0).
_ASIA_LIVE_JS = """<script>
(function(){
  /* 진입 위치 복원 (사용자 2026-06-15 '일본이면 일본·대만이면 대만') — 홈
     market.html 과 동일 메커니즘, 키만 noah_asia_scroll. async 콘텐츠 대비 다중 재적용. */
  try{
    if(history.scrollRestoration) history.scrollRestoration='manual';
    var SK='noah_asia_scroll', sy=sessionStorage.getItem(SK);
    var tgt=(sy!==null)?(parseInt(sy)||0):null, settled=(tgt===null), acted=false;
    ['wheel','touchstart','keydown'].forEach(function(ev){
      window.addEventListener(ev,function(){acted=true;settled=true;},{passive:true,once:true});
    });
    if(tgt!==null){
      var rr=function(){if(!acted)window.scrollTo(0,tgt);};
      rr(); [120,350,700,1200,2000].forEach(function(d){setTimeout(rr,d);});
      window.addEventListener('load',rr); setTimeout(function(){settled=true;},2100);
    }
    window.addEventListener('scroll',function(){if(settled){try{sessionStorage.setItem(SK,String(window.scrollY));}catch(_e){}}},{passive:true});
  }catch(_e){}
})();
</script>"""


def _render_asia_page(data: dict) -> str:
    """asia.html — 일·중·홍·대 업종 등락. 홈에서 분리해 홈 경량화(성능). 홈과
    동일 위젯 · 업데이트 배너(30분 체크) + 진입 위치 복원(사용자 2026-06-15)."""
    _lk2 = "color:var(--accent);font-size:13px;text-decoration:none;margin-left:8px"
    _lk = "color:var(--accent);font-size:13px;text-decoration:none;margin-left:10px"
    parts = [_MARKET_CSS]
    parts.append(
        '\n<div class="wrap">\n  <div class="nav">\n'
        '    <a href="market.html">🌍 홈</a>\n'
        '    &middot; <a href="index.html">🦉 종목분석</a>\n'
        '  </div>\n  <h1>🏯 아시아 업종 등락</h1>\n'
        '  <p class="sub">일본·중국·홍콩·대만 업종 등락 — 홈과 동일 소스 · 새 데이터 시 하단 알림(30분 체크)</p>\n'
        '  <div id="live-sections">')
    parts.append(_render_etf_sector_movers(
        data.get("jp_sector_movers", {}), "🇯🇵 일본 업종 등락 TOP 10",
        f'<a href="jp52" style="{_lk2}">📈 신고가·신저가</a>'
        f'<a href="jpmovers" style="{_lk2}">🚀 급등·급락</a>'))
    parts.append(_render_etf_sector_movers(
        data.get("cn_sector_movers", {}), "🇨🇳 중국 업종 등락 TOP 10",
        f'<a href="cn52" style="{_lk2}">📈 신고가·신저가</a>'      # 재도입 2026-06-17
        f'<a href="cnmovers" style="{_lk2}">🚀 급등·급락</a>'))
    parts.append(_render_etf_sector_movers(
        data.get("hk_sector_movers", {}), "🇭🇰 홍콩 업종 등락 TOP 10",
        f'<a href="hk52" style="{_lk2}">📈 신고가·신저가</a>'
        f'<a href="hkmovers" style="{_lk2}">🚀 급등·급락</a>'))
    _tw_link = (f'<a href="tw52" style="{_lk}">📈 신고가·신저가</a>'
                f'<a href="twhighlow" style="{_lk}">🚀 급등·급락</a>')
    parts.append(_render_etf_sector_movers(
        data.get("tw_sector_movers", {}), "🇹🇼 대만 업종 등락 TOP 10", _tw_link))
    parts.append("</div>\n</div>\n" + _ASIA_LIVE_JS + "\n</body></html>")
    return "".join(parts)


# ⚠️ 배포 주의: dashboard_server 는 on-demand 페이지에서 bot/* 모듈
# (finviz_client/us_pages/naver_*/earnings_calendar 등)을 import 한다 —
# auto-update 는 bot/*.py 가 하나라도 바뀌면 대시보드를 재시작한다
# (2026-06-11 트리거 확장, 실수기록 #11: '배포 완료 ≠ 서버 프로세스에 로드됨').


def regenerate_market_index() -> None:
    """Fetch market data and write market.html under ARCHIVE_ROOT."""
    try:
        from bot.market_overview import fetch_all_market_data
        data = fetch_all_market_data()
        # 30초 자동 swap 대신 업데이트 배너(30분 체크 → 사용자가 반영/유지 선택,
        # 사용자 2026-07-03) — market/asia 도 콘텐츠 페이지와 동일 패턴.
        html = _inject_update_banner(_render_market_page(data))
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        # 원자 쓰기 — 1분 주기로 빨라지며 half-write 읽힘 창 차단
        # (SV C-B 클래스, temp + os.replace).
        target = ARCHIVE_ROOT / "market.html"
        tmp = target.with_suffix(".html.tmp")
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(target)
        # ASIA 별도 대시보드도 같은 data 로 함께 렌더(추가 fetch 0, 사용자
        # 2026-06-15 — 홈에서 일·중·홍·대 분리). 실패해도 market.html 은 유지.
        try:
            atmp = (ARCHIVE_ROOT / "asia.html").with_suffix(".html.tmp")
            atmp.write_text(_inject_update_banner(_render_asia_page(data)),
                            encoding="utf-8")
            atmp.replace(ARCHIVE_ROOT / "asia.html")
        except Exception as aexc:
            log.warning("dashboard: asia.html regen failed: %s", aexc)
        log.info("dashboard: market.html + asia.html regenerated")
    except Exception as exc:
        log.warning("dashboard: market.html regen failed: %s", exc)


# ═════════════════════════════════════════════════════════════════════
# Lightweight stock lookup page (on-demand, any ticker)
# ═════════════════════════════════════════════════════════════════════

def resolve_name_to_ticker(query: str) -> str | None:
    """Resolve a Korean/English company name to a ticker symbol.
    Returns ticker string (e.g. '005930.KS') or None if not resolved."""
    if not query or not query.strip():
        return None
    q = query.strip()
    # English alias first (TSMC→2330.TW, TOYOTA→7203.T, etc.)
    try:
        from bot.market import resolve_english_alias
        t = resolve_english_alias(q)
        if t:
            return t
    except Exception:
        pass
    # Already looks like a ticker → skip further resolution
    if re.match(r'^[A-Z0-9][A-Z0-9.\-]{0,9}$', q.upper()):
        return None
    # KR name → DART corp_code
    try:
        from bot.dart_client import get_dart
        hits = get_dart().find_by_name(q)
        if hits and hits[0].get("stock_code"):
            code = hits[0]["stock_code"]
            try:
                from bot.market import normalize_kr_ticker_suffix
                resolved = normalize_kr_ticker_suffix(f"{code}.KS")
                if resolved:
                    return resolved
            except Exception:
                pass
            return f"{code}.KS"
    except Exception:
        pass
    return None


def _lookup_chart_html(ticker: str) -> str:
    """lookup 차트 섹션 HTML — 티커만 있으면 됨(스냅샷 불요). 점진 로딩
    (사용자 2026-06-10 '차트 먼저')용으로 shell 에서 즉시 렌더. /api/chart
    가 클라이언트에서 데이터 fetch."""
    _chart_payload = json.dumps({"times": [], "close": [], "ticker": ticker})
    _chart_payload = _chart_payload.replace("</", "<\\/")
    _tkr_esc = _html.escape(ticker)
    return f"""<section class="report-section">
    <h2>📈 가격 차트</h2>
    <div class="chart-toolbar">
      <span class="chart-tf-group">
        <button class="chart-tf-btn" data-kind="interval" data-val="1d">일봉</button>
        <button class="chart-tf-btn" data-kind="interval" data-val="1wk">주봉</button>
        <button class="chart-tf-btn" data-kind="interval" data-val="1mo">월봉</button>
      </span>
      <span class="chart-tf-sep">·</span>
      <span class="chart-tf-group">
        <button class="chart-tf-btn" data-kind="range" data-val="1d">1일</button>
        <button class="chart-tf-btn" data-kind="range" data-val="1wk">1주일</button>
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
        <div class="chart-head-row">
          <div id="chart-headline" class="chart-headline"></div>
          <span id="chart-live-slot"></span>
        </div>
        <div id="chart-ohlc" class="chart-ohlc"></div>
        <div id="price-chart" class="price-chart" data-ticker="{_tkr_esc}"></div>
        <div id="rsi-chart" class="sub-chart"></div>
        <div id="macd-chart" class="sub-chart"></div>
      </div>
      <div id="chart-values" class="chart-values"></div>
    </div>
    <div id="chart-caption" class="chart-caption"></div>
    <script type="application/json" id="chart-data">{_chart_payload}</script>
    <div id="chart-disc" class="chart-disc"></div>
</section>"""


def render_lookup_detail(ticker: str, enrich: bool = True) -> str:
    """지연로딩 detail fragment (data-lk 파트). 차트는 shell 이 먼저 렌더.

    2단계 로딩: phase=core(enrich=False)는 스냅샷만 — 앞쪽 탭 먼저. phase=full 은
    enrichment(뉴스·리서치)까지 포함해 교체. 라이브 quote 오버레이는 full 에만.
    스냅샷 실패(야후 차단 등) 시 직전 분석 스냅샷으로 폴백(분석 종목).

    ⚠️ 진행-로딩(per-pane 병렬·공유 스냅샷 캐시)은 동시 mutation race 로 상세
    전체가 죽어(사용자 2026-06-15 '새 종목 아예 안됨') 철회 — 순차 core→full 복원."""
    si = None
    try:
        from bot.stock_snapshot import collect_stock_snapshot
        si = collect_stock_snapshot(ticker)
    except Exception:
        pass
    if not si:        # 스냅샷 실패(야후 차단 등) → 직전 분석 스냅샷 폴백(분석 종목)
        si = _load_stored_stock_info(ticker)

    # Enrich missing tabs (news/research/consensus) on-demand — full 만
    if si and enrich:
        try:
            _ensure_detail_enrichment(ticker, si)
        except Exception:
            pass

    rec = {"ticker": ticker, "stock_info": si or {}}

    # Render stock info (header cards + tabs + panes)
    si_parts = _render_stock_info_html(rec)
    si_header = si_parts.get("header", "") if si_parts else ""
    si_tabs = si_parts.get("tabs", "") if si_parts else ""
    si_overview_card = si_parts.get("overview_card", "") if si_parts else ""
    si_tab_js = si_parts.get("tab_js", "") if si_parts else ""
    si_other = si_parts.get("other_panes", "") if si_parts else ""
    has_tabs = bool(si_parts)

    quote_script = (
        f'<script>var NOAH_TICKER={json.dumps(ticker)};'
        f'var NOAH_BASE="../";{_QUOTE_JS}</script>'
        if (has_tabs and enrich) else ""
    )

    _tkr_esc = _html.escape(ticker)
    # 종합 탭 = 차트만 — 회사 설명은 기업 탭과 중복이라 제거(사용자 2026-06-11).

    # ── 지연로딩 fragment: data-lk 파트 분할 — shell JS 가 슬롯에 제자리
    # 주입(헤더/탭/기타 pane 위치 고정, 차트는 shell 소유라 안 움직임).
    if not has_tabs:
        return (f'<div data-lk="desc"><div class="lk-loading">⚠️ {_tkr_esc} '
                '기본 정보를 불러오지 못했습니다 (차트는 위에 표시).</div></div>')
    return (
        f'<div data-lk="header">{si_header}</div>\n'
        f'<div data-lk="tabs">{si_tabs}</div>\n'
        f'<div data-lk="desc">{si_overview_card}</div>\n'
        f'<div data-lk="other">{si_other}</div>\n'
        f'<script>{_DETAIL_DEEP_LINK_JS}</script>\n{si_tab_js}\n{quote_script}'
    )


# 지연로딩 shell JS — detail fragment(core→full 2단계)를 data-lk 파트별
# 슬롯(lk-header/tabs/desc/other-slot)에 제자리 주입 + 스크립트 순차
# 재실행(src 는 onload 대기 → LWC 라이브러리 후 _CHART_JS 순서 보존).
# application/json 데이터 블록(차트 payload)은 실행 건너뛰고 DOM 보존.
_LOOKUP_LAZY_TMPL = r'''<script>(function(){var T=__TICKER__;
function runScripts(c){var ss=[].slice.call(c.querySelectorAll('script'));var i=0;
function next(){if(i>=ss.length)return;var o=ss[i++];
var ty=(o.getAttribute('type')||'').toLowerCase();
if(ty&&ty!=='text/javascript'&&ty!=='application/javascript'){next();return;}
var s=document.createElement('script');
for(var k=0;k<o.attributes.length;k++)s.setAttribute(o.attributes[k].name,o.attributes[k].value);
if(o.src){s.onload=next;s.onerror=next;}else{s.textContent=o.textContent;}
if(o.parentNode)o.parentNode.replaceChild(s,o);else c.appendChild(s);
if(!o.src)next();}next();}
function inject(h){
  var tmp=document.createElement('div');tmp.innerHTML=h;
  /* 스크립트는 먼저 분리(슬롯 이동 시 미실행 방지) → 파트 배치 후 실행 */
  var sc=document.createElement('div');
  [].slice.call(tmp.querySelectorAll('script')).forEach(function(s){sc.appendChild(s);});
  function put(part,slotId){var src=tmp.querySelector('[data-lk="'+part+'"]');
    var dst=document.getElementById(slotId);if(!src||!dst)return;
    dst.innerHTML='';while(src.firstChild)dst.appendChild(src.firstChild);}
  put('header','lk-header-slot');put('tabs','lk-tabs-slot');
  put('desc','lk-desc-slot');put('other','lk-other-slot');
  /* 라이브 점·🔄 를 주입 즉시 차트 헤드라인 슬롯으로 — _QUOTE_JS(relocate)
     는 full 단계에만 실려 와 core 동안 탭 위에 떠 보이던 것 차단(사용자
     2026-06-11). full 재주입 시 슬롯 비우고 새 배지로 교체(중복 방지). */
  var slot=document.getElementById('chart-live-slot');
  var stat=document.querySelector('.si-quote-status');
  if(slot&&stat){slot.innerHTML='';while(stat.firstChild)slot.appendChild(stat.firstChild);stat.style.display='none';}
  document.body.appendChild(sc);runScripts(sc);
}
function get(phase){return fetch('../api/lookup_detail?ticker='+encodeURIComponent(T)+'&phase='+phase)
  .then(function(r){if(!r.ok)throw 0;return r.text();});}
/* 2단계: core(스냅샷 — 헤더·탭·앞쪽 탭 먼저) → full(뉴스·리서치 enrichment 포함
   교체) — **순차**. 병렬 per-pane 진행로딩은 공유 스냅샷 동시 mutation race 로
   상세 전체가 죽어(사용자 2026-06-15 '새 종목 아예 안됨') 철회. */
get('core').then(function(h){inject(h);return get('full');})
.then(function(h){inject(h);})
.catch(function(){
  /* core 실패 시 full 단독 재시도 → 그래도 실패면 안내 */
  get('full').then(function(h){inject(h);}).catch(function(){
    var d=document.getElementById('lk-desc-slot');
    if(d)d.innerHTML='<div class="lk-loading">⚠️ 상세 정보를 불러오지 못했습니다. <a href="" onclick="location.reload();return false">다시 시도</a></div>';});
});
})();</script>'''

_LOOKUP_SEARCH_JS = """<script>
var lkInp=document.getElementById('lk-search');
var lkBtn=document.getElementById('lk-go');
function lkGo(){var q=(lkInp.value||'').trim();
if(!q)return;
lkBtn.disabled=true;lkBtn.textContent='…';
fetch('../api/search?q='+encodeURIComponent(q))
.then(function(r){return r.json();})
.then(function(d){
  window.location.href='../lookup/'+encodeURIComponent(d.ticker||q);
}).catch(function(){
  window.location.href='../lookup/'+encodeURIComponent(q);
});}
lkBtn.addEventListener('click',lkGo);
lkInp.addEventListener('keydown',function(e){if(e.key==='Enter')lkGo();});
</script>"""


def render_lookup_page(ticker: str) -> str:
    """지연로딩 shell — 즉시 반환(이름 + 검색 + 스피너). 무거운 detail(스냅샷
    +enrichment+차트+탭)은 /api/lookup_detail 로 비동기 로드. 첫 바이트까지
    10~20초 멈춰있던 lookup 을 즉시 인터랙티브하게(사용자 2026-06-10)."""
    kr_name = _ticker_display_name(ticker)
    h1_label = f"{kr_name} / {ticker}" if kr_name else ticker
    # 차트는 shell 에서 즉시 렌더(티커만 필요) → 점진 로딩. 무거운 detail
    # (스냅샷+탭)은 슬롯별 비동기(core→full 2단계). 위치는 처음부터 고정.
    chart_section = _lookup_chart_html(ticker)
    chart_scripts = (
        f'<script src="../{_LWC_LIB_NAME}"></script>\n<script>{_CHART_JS}</script>'
    )
    lazy_js = _LOOKUP_LAZY_TMPL.replace("__TICKER__", json.dumps(ticker))
    # 현재가·시총 즉시 채움 — LIGHT quote(~0.5-1s, 5분 캐시)로 헤더 스켈레톤
    # 의 두 카드만 먼저(사용자 2026-06-11 '두번째 캡처 먼저, 차트 다음 바로').
    # detail(core/full) 도착 시 전체 헤더로 교체되며 applyFmt 가 재적용.
    qfast_js = (
        "<script>(function(){fetch('../api/quote?ticker='+encodeURIComponent("
        + json.dumps(ticker) + "),{cache:'no-store'})"
        ".then(function(r){return r.json();})"
        ".then(function(j){if(!j||!j.ok||!j.quote||!j.quote.fmt)return;"
        "var f=j.quote.fmt;['price','mcap'].forEach(function(k){"
        "var e=document.querySelector('#lk-header-slot [data-q=\"'+k+'\"]');"
        "if(e&&f[k])e.textContent=f[k];});})"
        ".catch(function(){});})();</script>"
    )
    save_js = (
        "<script>(function(){var btn=document.getElementById('lk-save');"
        "if(!btn)return;var ticker=" + json.dumps(ticker) + ";"
        "btn.addEventListener('click',function(){btn.disabled=true;btn.textContent='…';"
        "fetch('../api/favorite_add',{method:'POST',headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify({ticker:ticker})}).then(function(r){return r.json();})"
        ".then(function(d){if(d.ok){btn.textContent='✅ 저장됨';btn.style.background='var(--accent)';btn.style.color='#fff';}"
        "else{btn.textContent=(d.error==='duplicate or fetch failed')?'⭐ 이미 저장됨':'⭐ 실패';btn.disabled=false;}})"
        ".catch(function(){btn.textContent='⭐ 실패';btn.disabled=false;});});})();</script>"
    )
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>📊 {_html.escape(h1_label)} — Lookup</title>
<script>{_THEME_JS}</script>
<style>{_DETAIL_CSS}
.lk-loading{{padding:48px 20px;text-align:center;color:var(--muted);font-size:14px}}</style>
</head>
<body>
<div class="wrap">
  <a class="back" href="../market.html">← 시장 개요로 돌아가기</a>
  <div style="margin:10px 0 16px;display:flex;gap:8px">
    <input id="lk-search" type="text" value="{_html.escape(ticker)}"
      placeholder="티커 또는 종목명 검색 (예: NVDA, 삼성전자, 7203.T)"
      autocomplete="off" spellcheck="false"
      style="flex:1;padding:8px 12px;font-size:14px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);outline:none">
    <button id="lk-go" style="padding:8px 16px;font-size:13px;font-weight:600;border:none;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer">검색</button>
    <button id="lk-save" style="padding:8px 16px;font-size:13px;font-weight:600;border:none;border-radius:8px;background:var(--card);color:var(--accent);border:1px solid var(--accent);cursor:pointer">⭐ 저장</button>
  </div>
  <div class="title-row">
    <h1>📊 {_html.escape(h1_label)}</h1>
  </div>
  <div id="lk-header-slot"><div class="si-header">
    <div class="si-card"><span class="si-label">현재가</span><span class="si-value" data-q="price">—</span></div>
    <div class="si-card"><span class="si-label">시가총액</span><span class="si-value" data-q="mcap">—</span></div>
    <div class="si-card"><span class="si-label">발행주식수</span><span class="si-value">—</span></div>
    <div class="si-card"><span class="si-label">다음 실적</span><span class="si-value">—</span></div>
  </div></div>
  <div id="lk-tabs-slot"><div class="si-tabs"><button type="button" class="si-tab active">종합</button><span style="align-self:center;font-size:12px;color:var(--muted);padding:0 10px">재무·실적·리서치 등 로딩 중…</span></div></div>
  <div class="si-pane active" id="si-overview">
    {chart_section}
    <div id="lk-desc-slot"></div>
  </div>
  <div id="lk-other-slot"></div>
</div>
{chart_scripts}
{qfast_js}
{lazy_js}
{_LOOKUP_SEARCH_JS}
{save_js}
</body>
</html>
"""
