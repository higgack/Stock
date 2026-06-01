"""HSK(10-digit) → 산업분류(MTI 기반) bridge.

The 관세청 customs API is keyed by HS code, but the industry-trend
dashboard (반도체·자동차·이차전지 …, mirroring 산업부 수출동향) needs
figures aggregated by INDUSTRY. The official bridge is the 무역협회
HSK-MTI 연계표: every 10-digit HSK maps to a 6-digit MTI code and a
top-level industry label (the '구분' column — 반도체, 자동차, 화장품 …).

We ship the linkage as a flat TSV (trade/data/hsk_mti.tsv, converted
once from the published xlsx) so there's no openpyxl dependency at
runtime and the mapping is reviewable in git. Each line:

    <HSK 10-digit>\\t<MTI 6-digit>\\t<산업분류>

21 industries in the 2026 table. A given HS leaf belongs to exactly one
industry, so industry aggregation is: for each scanned leaf, look up its
industry and sum exports into that bucket. Leaves not in the table (or in
the catch-all '기타') are excluded from the named-industry view.

Used by:
  - trade/scripts/scan_customs.py — aggregates the chapter sweep it
    already fetches into per-industry monthly series (no extra API calls)
  - the dashboard 산업트렌드 tab

Refresh: when 무역협회 republishes the 연계표 (annual, ~HS revision),
re-run the converter and commit the new TSV. The 기준년도 is recorded in
the file header so drift is visible.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Shipped reference file (committed). Env override for tests / a newer
# table dropped on the host without a redeploy.
_PKG_DIR = Path(__file__).resolve().parent
DEFAULT_PATH = Path(
    os.environ.get("TRADE_HSK_MTI_PATH") or (_PKG_DIR / "data" / "hsk_mti.tsv")
)

# The linkage's catch-all bucket — real industries only, so the dashboard
# doesn't show a giant '기타' that drowns the named sectors.
CATCH_ALL = "기타"


class HskMtiFileMissing(FileNotFoundError):
    """Raised when the linkage TSV is absent so callers can degrade
    gracefully (skip the industry view) instead of crashing the scan."""


@lru_cache(maxsize=4)
def _load(path_str: str) -> dict[str, str]:
    """Return {hsk_10digit: industry}. Cached per path; the file is
    static so a process-lifetime cache is safe."""
    path = Path(path_str)
    if not path.exists():
        raise HskMtiFileMissing(str(path))
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        hsk = parts[0].strip()
        industry = parts[2].strip()
        if hsk and industry:
            out[hsk] = industry
    return out


def load(path: Path | str = DEFAULT_PATH) -> dict[str, str]:
    """{HSK(10-digit): 산업분류}. Raises HskMtiFileMissing if absent."""
    return _load(str(path))


def industry_of(hsk: str, path: Path | str = DEFAULT_PATH) -> str | None:
    """Industry label for a 10-digit HSK, or None when unmapped.

    The customs API returns leaf HS codes; we match the full 10-digit
    code. (Aggregation upstream sums leaves, so we never need prefix
    matching here.)"""
    if not hsk:
        return None
    return load(path).get(str(hsk).strip())


def industries(path: Path | str = DEFAULT_PATH,
               include_catch_all: bool = False) -> list[str]:
    """Sorted unique industry labels. Excludes '기타' by default."""
    seen = set(load(path).values())
    if not include_catch_all:
        seen.discard(CATCH_ALL)
    return sorted(seen)
