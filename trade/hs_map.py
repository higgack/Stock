"""Operator-curated 품목명 → HS코드 mapping.

The trade-bot's alerts carry BeOn's free-text Korean item names
("라면", "음극재 (천연흑연)") — never an HS code. The 관세청
수출입실적 OpenAPI, by contrast, is keyed entirely by HS code. To pull
official monthly numbers for a BeOn item we therefore need a bridge,
and that bridge cannot be auto-generated reliably (BeOn item names are
trade nicknames, not the official HS descriptions). So it is
operator-curated and opt-in: only items the operator explicitly pins
get enriched.

State lives in plain text at `~/.trade/hs_map.tsv`, one
`<품목명><TAB><HS코드>` per line. Comments (`#`) and blank lines are
skipped so the operator can hand-edit. This mirrors `trade/ignored.py`
(plain-file source of truth + DM commands that edit it).

Lookup is whitespace-normalised and case-folded so the pinned key
matches the `item` field that `parser.parse_caption` stores in
store.db (`_norm_ws(item_raw)`), regardless of stray spacing.

Used by:
  - trade/scripts/fetch_customs.py — resolves which HS codes to pull
  - bot DM commands /hs, /unhs, /hslist (added when the customs feature
    is wired; this module is the storage layer they sit on)

Reversal is always safe: `/unhs <품목>` or deleting the line drops the
mapping; the next customs fetch simply skips that item. No alert data
or store.db row is ever touched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_DEFAULT_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
DEFAULT_PATH = _DEFAULT_DIR / "hs_map.tsv"

# 관세청 HS codes are published at 2 / 4 / 6 / 10-digit granularity. We
# accept any of those lengths; the 수출입실적 API takes the prefix and
# aggregates accordingly. Stored as a STRING so leading zeros survive
# (e.g. '0902' tea) — never coerce to int.
_VALID_HS_LENGTHS = (2, 4, 6, 10)
_HS_RE = re.compile(r"^\d+$")


def _norm(item: str) -> str:
    """Normalise an item name for matching.

    Collapses internal whitespace + strips + case-folds, so a pinned
    'LiFePO4 양극재' matches store.db's `_norm_ws`-collapsed item even
    if the operator typed extra spaces or different case in the ASCII
    portion. Korean text is unaffected by casefold.
    """
    return re.sub(r"\s+", " ", item).strip().lower()


def is_valid_hs(code: str) -> bool:
    """True if `code` is a plausible HS code: digits only, length in
    {2,4,6,10}. Rejects company names, partial codes, dotted forms
    ('1902.30') — the operator must supply the bare digit string."""
    if not code:
        return False
    return bool(_HS_RE.match(code)) and len(code) in _VALID_HS_LENGTHS


def _ensure(path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()
    return p


def load(path: Path | str = DEFAULT_PATH) -> dict[str, str]:
    """Return {normalised_item: hs_code}. Malformed / commented / blank
    lines are skipped silently (hand-edit friendly). On a duplicate
    normalised key the LAST line wins — same as a fresh re-pin."""
    p = _ensure(path)
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Split on the first TAB; tolerate accidental multiple tabs /
        # trailing whitespace around the code.
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        item = parts[0].strip()
        code = parts[1].strip()
        if not item or not is_valid_hs(code):
            continue
        out[_norm(item)] = code
    return out


def entries(path: Path | str = DEFAULT_PATH) -> list[tuple[str, str]]:
    """Return [(original_item, hs_code)] in file order for display.

    Keeps the operator's original spelling (load() normalises keys for
    matching; this preserves the human-readable form for /hslist)."""
    p = _ensure(path)
    out: list[tuple[str, str]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        item = parts[0].strip()
        code = parts[1].strip()
        if not item or not is_valid_hs(code):
            continue
        out.append((item, code))
    return out


def get(item: str, path: Path | str = DEFAULT_PATH) -> str | None:
    """HS code pinned for `item` (whitespace/case-normalised), or None."""
    if not item:
        return None
    return load(path).get(_norm(item))


def add(item: str, hs_code: str, path: Path | str = DEFAULT_PATH) -> bool:
    """Pin (or re-pin) `item` → `hs_code`.

    Returns True if the file changed (new pin or code updated), False if
    the identical mapping was already present. Raises ValueError on an
    invalid HS code so the DM command can surface a clear message.

    Re-pinning the same item to a new code rewrites that item's line
    rather than appending a duplicate, so the file stays one-line-per-item.
    """
    item = (item or "").strip()
    hs_code = (hs_code or "").strip()
    if not item:
        raise ValueError("empty item")
    if not is_valid_hs(hs_code):
        raise ValueError(f"invalid HS code: {hs_code!r}")

    key = _norm(item)
    existing = entries(path)
    rewritten: list[tuple[str, str]] = []
    found = False
    changed = False
    for it, code in existing:
        if _norm(it) == key:
            found = True
            if code != hs_code:
                changed = True
            rewritten.append((item, hs_code))  # keep latest spelling+code
        else:
            rewritten.append((it, code))
    if not found:
        rewritten.append((item, hs_code))
        changed = True
    if not changed:
        return False
    _write(path, rewritten)
    return True


def remove(item: str, path: Path | str = DEFAULT_PATH) -> bool:
    """Drop the pin for `item`. Returns True if a mapping was removed,
    False if it wasn't present (idempotent)."""
    item = (item or "").strip()
    if not item:
        return False
    key = _norm(item)
    existing = entries(path)
    kept = [(it, code) for it, code in existing if _norm(it) != key]
    if len(kept) == len(existing):
        return False
    _write(path, kept)
    return True


def _write(path: Path | str, pairs: list[tuple[str, str]]) -> None:
    """Rewrite the whole file from (item, code) pairs. Atomic via
    temp-file + replace so a crash mid-write can't truncate the map."""
    p = _ensure(path)
    body = "".join(f"{it}\t{code}\n" for it, code in pairs)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
