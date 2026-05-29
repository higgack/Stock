"""Bottleneck Screener theme registry — Wave 1 (5 domains).

Each domain lives in its own module (bot/screener_themes/<slug>.py) as a
top-level ``THEME`` dict with five required keys:

  - ``domain`` (str)                — display name surfaced to Telegram + dashboard
  - ``horizon`` (str)               — e.g. "6-18 months"; used by Pro for tense / kill-trigger
  - ``binding_layer_taxonomy`` (list[str]) — 4-8 niche sub-layers (Theory of Constraints)
  - ``catalyst_types`` (list[str])  — 5-7 leading-signal categories (Tier A weight)
  - ``regional_concentration`` (dict[str, str]) — SPOF map: layer → "REGION (Ticker NAME / Ticker NAME)"

Optionally:
  - ``aliases`` (list[str])         — extra strings that route to this theme via /screener

Resolution order in ``resolve()``:
  1) exact slug match (filename without ``.py``, lowercased)
  2) ``aliases`` exact match (lowercased)
  3) fuzzy: substring match against slug or aliases (only when unambiguous —
     two hits → return None so the user sees an error rather than the wrong theme)

Adding a new theme = drop a new module here. No edits to bot/screener.py or
bot/telegram_bot.py needed — the registry auto-discovers via pkgutil. This
keeps Wave 2/3/∞ domain additions structural (one file per theme), not
sprinkled across the orchestrator.

A registry-level smoke test on import validates each module's THEME shape
so a typo (e.g. ``bindng_layer_taxonomy``) fails fast at startup, not three
minutes into a Pro call.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Optional

log = logging.getLogger("bot.screener_themes")

# Slugs are derived from filenames at import time below. Aliases populate
# the lookup table once. Both maps are lowercased keys for case-insensitive
# /screener input ('/screener EV' == '/screener ev' == '/screener 전기차').
_THEMES: dict[str, dict] = {}
_ALIAS_TO_SLUG: dict[str, str] = {}


_REQUIRED_KEYS = (
    "domain",
    "horizon",
    "binding_layer_taxonomy",
    "catalyst_types",
    "regional_concentration",
)

# 3-layer 도메인 모델 (2026-05-29 사용자 정식 분류 = 미국 GICS-like):
#  - L1_TREND   : cross-cutting cycle 베팅, 공식 sector 분류 외 (AI DC /
#                 EV / 방산 / 바이오 cycle / 신재생 / 로봇 등).
#  - L2_SECTOR  : 11 공식 sector (Industrials / Health Care / Financials
#                 / Consumer Discretionary / Consumer Staples / Energy /
#                 Basic Materials / Real Estate / Utilities / Communication
#                 Services / Technology).
#  - L3_INDUSTRY: 각 L2 아래 sub-industry (예: Industrials → Aerospace
#                 & Defense / Airlines / Building Products ...). 더
#                 깊이는 없음 (L4 미사용).
#  - AD_HOC     : `/screener <자유어>` Phase 0 즉석 생성 도메인. 캐시
#                 24h, audit log, 정식 모듈 promote 는 user 수동. 2026-
#                 05-29 신설.
# Layer 표기 누락은 default L1_TREND 로 fallback (back-compat).
_VALID_LAYERS = ("L1_TREND", "L2_SECTOR", "L3_INDUSTRY", "AD_HOC")


def _validate(slug: str, theme: dict) -> None:
    """Fail-fast import-time check. Catches typos in dict keys before they
    cost 3 minutes of Pro latency. Layer taxonomy lower bound is 3 so the
    Pro has enough niche signal to surface 4-6 sub-themes."""
    for k in _REQUIRED_KEYS:
        if k not in theme:
            raise ValueError(f"theme '{slug}' missing required key: {k}")
    # Min 2 layers (Energy + Real Estate L2 in user's official taxonomy
    # each have only 2 L3 sub-industries — Oil/Gas + Energy Equipment;
    # Real Estate Services + REITs). 2026-05-29 user taxonomy update
    # relaxed from min 3 to accommodate the official sector structure.
    if not isinstance(theme["binding_layer_taxonomy"], list) or len(theme["binding_layer_taxonomy"]) < 2:
        raise ValueError(f"theme '{slug}' binding_layer_taxonomy must be a list of ≥2 layers")
    if not isinstance(theme["catalyst_types"], list) or len(theme["catalyst_types"]) < 3:
        raise ValueError(f"theme '{slug}' catalyst_types must be a list of ≥3 catalysts")
    # Min 2 SPOF entries — Energy/Real Estate L2 each have 2 L3 sub-
    # industries and a matching 2-entry regional map. Relaxed alongside
    # binding_layer_taxonomy 2026-05-29.
    if not isinstance(theme["regional_concentration"], dict) or len(theme["regional_concentration"]) < 2:
        raise ValueError(f"theme '{slug}' regional_concentration must be a dict of ≥2 SPOF entries")
    layer = theme.get("layer")
    if layer is not None and layer not in _VALID_LAYERS:
        raise ValueError(
            f"theme '{slug}' layer={layer!r} not in {_VALID_LAYERS}"
        )


def _discover() -> None:
    """Walk the package, import each submodule, register its THEME dict.
    Idempotent — re-running on hot-reload is safe (dict overwrite).
    Submodules without a top-level THEME are skipped (the registry helpers
    themselves live alongside theme files)."""
    if _THEMES:
        return  # already discovered
    pkg = importlib.import_module(__name__)
    for finder, mod_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if is_pkg or mod_name.startswith("_"):
            continue
        full_name = f"{__name__}.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:
            log.exception("screener_themes: failed to import %s: %s", full_name, exc)
            continue
        theme = getattr(mod, "THEME", None)
        if not isinstance(theme, dict):
            continue
        slug = mod_name.lower()
        try:
            _validate(slug, theme)
        except ValueError as exc:
            log.error("screener_themes: skipping invalid theme '%s': %s", slug, exc)
            continue
        _THEMES[slug] = theme
        _ALIAS_TO_SLUG[slug] = slug
        for alias in theme.get("aliases", []) or []:
            a = str(alias).strip().lower()
            if a and a not in _ALIAS_TO_SLUG:
                _ALIAS_TO_SLUG[a] = slug
    log.info("screener_themes: discovered %d themes — %s",
             len(_THEMES), ", ".join(sorted(_THEMES)))


def resolve(name: str) -> Optional[dict]:
    """Look up a theme by slug or alias (case-insensitive). Returns the
    theme dict or None when no theme matches. Substring fuzzy match is
    deliberately conservative — two ambiguous hits return None so the
    user gets an error + domain list rather than a wrong-domain run."""
    _discover()
    if not name:
        name = "bottleneck"
    key = name.strip().lower()
    slug = _ALIAS_TO_SLUG.get(key)
    if slug:
        return _THEMES.get(slug)
    # Conservative substring fallback — only succeeds when exactly one
    # alias/slug contains the query.
    hits = [s for k, s in _ALIAS_TO_SLUG.items() if key in k]
    uniq = set(hits)
    if len(uniq) == 1:
        return _THEMES.get(next(iter(uniq)))
    return None


def resolve_slug(name: str) -> Optional[str]:
    """Same resolution rules as ``resolve()`` but returns the canonical
    slug instead of the theme dict. Used by ``bot.screener_cache`` so
    alias inputs ('/screener AI 데이터센터', '/screener ai_datacenter') 가
    같은 cache 행 (bottleneck) 으로 collapse 된다."""
    _discover()
    if not name:
        name = "bottleneck"
    key = name.strip().lower()
    slug = _ALIAS_TO_SLUG.get(key)
    if slug:
        return slug
    hits = [s for k, s in _ALIAS_TO_SLUG.items() if key in k]
    uniq = set(hits)
    if len(uniq) == 1:
        return next(iter(uniq))
    return None


def list_domains() -> list[dict]:
    """Return a list of ``{"slug", "domain", "aliases"}`` for each
    registered theme, sorted by slug. Used by /screener error messages
    and by _HELP_TEXT generation to keep the public domain list synced
    with the registry without hand-editing."""
    _discover()
    out = []
    for slug in sorted(_THEMES):
        theme = _THEMES[slug]
        out.append({
            "slug": slug,
            "domain": theme.get("domain", slug),
            "aliases": list(theme.get("aliases", []) or []),
            "layer": theme.get("layer", "L1_TREND"),
        })
    return out


def available_summary() -> str:
    """One-line user-facing summary of registered domains for error
    messages: '/screener {bottleneck | ev | defense | pharma | solar}'."""
    _discover()
    return "/screener {" + " | ".join(sorted(_THEMES)) + "}"
