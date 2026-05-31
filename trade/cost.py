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

    # API daily calls ≈ pinned items (customs-fetch makes one call per pin
    # per day; the window is a single ranged request). HS map missing /
    # empty → 0.
    try:
        pins = len(hs_map.entries())
    except Exception:
        pins = 0

    return {
        "files": files,            # {label: bytes}
        "media_bytes": media,
        "data_total_bytes": data_total,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "pins": pins,
        "api_calls_per_day": pins,
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
        f"  └ 일 호출 ≈ <b>{s['api_calls_per_day']}</b>회 / 한도 "
        f"{s['api_quota']:,} (핀 {s['pins']}개)",
        "• Telegram · HS부호 파일: 무료",
        "• LLM: 없음 (trade 봇은 토큰 과금 0)",
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
        "💰 외부 API 무료",
        f"관세청 일 {s['api_calls_per_day']}/{s['api_quota']:,}콜",
        f"데이터 {fmt_bytes(s['data_total_bytes'])}",
    ]
    if s["disk_total_bytes"]:
        parts.append(f"디스크 잔여 {fmt_bytes(s['disk_free_bytes'])}")
    return " · ".join(parts)
