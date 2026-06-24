"""Cost & resource status for the trade-bot.

The operator asked for a 'cost' view. The honest answer for THIS bot is:
every external dependency is free — so this module reports that fact
plus the real resources worth watching (disk growth, API-quota headroom),
rather than inventing dollar figures that don't exist.

Why free (verified):
  - 관세청 data.go.kr API (수출입실적 / HS부호): public data, '이용허락범위
    제한 없음', no metered billing — only a daily call quota (commonly
    10,000/day on the free tier).
  - Telegram Bot API + Telethon: free.
  - HS code reference file: free download.
  - NO LLM: unlike the NOAH stock-bot (Gemini, per-token billing), the
    trade-bot calls no paid model. Token cost is structurally zero.

The genuine resource concerns are local:
  - Disk: inbox.jsonl + media/ + store.db + customs.db accumulate.
  - API quota: pinned-item count == daily customs calls.

Both surface here and in the dashboard header so the operator can spot a
disk squeeze or a quota approach before it bites — that's the real
'cost' to manage here.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from trade import hs_map


_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")

# Free-tier daily call quota for data.go.kr (typical). Override via env if
# the operator's account differs. Used only to show headroom — we don't
# enforce it (the API itself does).
API_DAILY_QUOTA = int(os.environ.get("TRADE_DATA_GO_KR_QUOTA") or "10000")

# Files whose growth is worth watching. Label → path.
_WATCHED = {
    "store.db": _DATA_DIR / "store.db",
    "customs.db": _DATA_DIR / "customs.db",
    "inbox.jsonl": _DATA_DIR / "inbox.jsonl",
}
_MEDIA_DIR = _DATA_DIR / "media"


def _file_bytes(p: Path) -> int:
    try:
        return p.stat().st_size if p.exists() else 0
    except OSError:
        return 0


def _dir_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def fmt_bytes(n: int) -> str:
    """1536 → '1.5KB', 1419595 → '1.4MB'."""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(f)}B"
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def collect() -> dict:
    """Gather the cost/resource snapshot. Pure read-only stat() calls —
    no DB queries, no network — so it's cheap to call from a command or
    the dashboard render. Returns a dict the formatters below consume."""
    files = {label: _file_bytes(p) for label, p in _WATCHED.items()}
    media = _dir_bytes(_MEDIA_DIR)
    data_total = sum(files.values()) + media

    try:
        du = shutil.disk_usage("/")
        disk_free = du.free
        disk_total = du.total
    except OSError:
        disk_free = disk_total = 0

    try:
        pins = len(hs_map.entries())
    except Exception:
        pins = 0

    # Daily 관세청 API call estimate. customs-fetch.timer fires 4×/day and
    # each tick runs:
    #   - fetch_customs   : 1 call per pinned item
    #   - scan_customs    : full chapter sweep (97 chapters, +pagination on
    #                       heavy chapters → ~1.3× calls)
    #   - industry_report : same sweep split into 2 ≤12-month windows (YoY)
    # so a tick ≈ pins + 97×1.3 + 97×2×1.3 calls. ×4 ticks/day. This is an
    # estimate (page counts vary by chapter), labelled as such in the UI.
    _TICKS = 4
    _CHAPTERS = 97
    _PAGE_FACTOR = 1.3          # avg pages per chapter (most 1, heavy ones >1)
    scan_calls = round(_CHAPTERS * _PAGE_FACTOR)
    industry_calls = round(_CHAPTERS * 2 * _PAGE_FACTOR)   # 2 windows
    per_tick = pins + scan_calls + industry_calls
    api_calls_per_day = per_tick * _TICKS

    try:
        from trade import llm_usage
        # 수출입 대시보드 LLM = 트레이드 insight(🔍산업 추가신호)만. kg 관계후보
        # (kg_blog/kg_dart)는 블로그·DART 대시보드 카드로 분리 집계(사용자
        # 2026-06-24 '각 대시보드 자기 비용'). 메인 합산은 둘 다 포함(별 버킷).
        llm = llm_usage.summary(exclude_kinds=llm_usage.KG_KINDS)
    except Exception:
        llm = None

    return {
        "files": files,            # {label: bytes}
        "media_bytes": media,
        "data_total_bytes": data_total,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "pins": pins,
        "llm": llm,                # None or {today/d7/d30, total_calls}
        "api_calls_per_day": api_calls_per_day,   # estimate (sweeps + pins)
        "api_calls_breakdown": {
            "ticks_per_day": _TICKS,
            "pins_per_tick": pins,
            "scan_per_tick": scan_calls,
            "industry_per_tick": industry_calls,
        },
        "api_quota": API_DAILY_QUOTA,
    }


def format_telegram(snap: dict | None = None) -> str:
    """HTML body for the /cost DM command."""
    s = snap or collect()
    files = s["files"]
    lines = [
        "💰 <b>비용 · 자원 현황</b>",
        "",
        "<b>외부 API — 전부 무료</b>",
        "• 관세청 data.go.kr: 무료 (공공데이터, 과금 없음)",
        f"  └ 일 호출 ≈ <b>{s['api_calls_per_day']:,}</b>회 / 한도 "
        f"{s['api_quota']:,} (추정)",
        (f"     4회×(핀 {s['api_calls_breakdown']['pins_per_tick']} + 스캔 "
         f"{s['api_calls_breakdown']['scan_per_tick']} + 산업 "
         f"{s['api_calls_breakdown']['industry_per_tick']})"
         if s.get("api_calls_breakdown") else ""),
        "• Telegram · HS부호 파일: 무료",
    ]
    llm = s.get("llm")
    if llm and llm.get("total_calls"):
        d30, td = llm["d30"], llm["today"]
        lines.append(
            f"• LLM (Gemini, 🔍산업 추가신호): 30일 <b>{d30['calls']}</b>콜 · "
            f"<b>{d30['cost_krw']:,}</b>원 (오늘 {td['calls']}콜), 데이터 변동 시만"
        )
    else:
        lines.append("• LLM (Gemini, 🔍추가신호): 호출 0 — 데이터 변동 시만, 사실상 무료")
    lines += [
        "",
        "<b>실제 자원 (로컬 디스크)</b>",
    ]
    for label, b in files.items():
        lines.append(f"• {label}: {fmt_bytes(b)}")
    lines.append(f"• media/: {fmt_bytes(s['media_bytes'])}")
    lines.append(f"• 데이터 합계: <b>{fmt_bytes(s['data_total_bytes'])}</b>")
    if s["disk_total_bytes"]:
        lines.append(
            f"• 디스크 잔여: <b>{fmt_bytes(s['disk_free_bytes'])}</b> / "
            f"{fmt_bytes(s['disk_total_bytes'])}"
        )
    return "\n".join(lines)


def format_dashboard_line(snap: dict | None = None) -> str:
    """One compact line for the dashboard header. Plain text (caller
    escapes / wraps). Empty string only if collect() truly failed."""
    s = snap or collect()
    parts = [
        f"💰 관세청 API 무료 ~{s['api_calls_per_day']:,}/{s['api_quota']:,}콜",
    ]
    # LLM(Gemini 🔍추가신호)은 변동 시에만 — 무료 아님이라 비용을 함께 노출
    llm = s.get("llm")
    if llm and llm.get("total_calls"):
        d30 = llm["d30"]
        parts.append(f"LLM 30일 {d30['cost_krw']:,}원({d30['calls']}콜)")
    else:
        parts.append("LLM 0원(변동시만)")
    parts.append(f"데이터 {fmt_bytes(s['data_total_bytes'])}")
    if s["disk_total_bytes"]:
        parts.append(f"디스크 잔여 {fmt_bytes(s['disk_free_bytes'])}")
    return " · ".join(parts)
