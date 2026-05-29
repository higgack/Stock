"""Bottleneck Screener 24h disk cache (정적 + 자유어 도메인 공통).

User policy 2026-05-29: 같은 도메인 같은 KST 일자 재호출 시 Pro 5-phase
파이프라인 skip + 캐시된 ScreenerResult 즉시 반환 (₩0). NOAH /ticker 의
디스크 캐시 패턴 mirror — screener 도 같은 일자 안에서 결정적 출력
보장 (6-24m thesis 라 intra-day 가격 변동 영향 minimal).

Cache key:
  • 정적 도메인: slug ('bottleneck' / 'ev' / 'quantum' 등)
  • 자유어 도메인: theme dict 의 freetext cache_key (sha256[:12])
  • 자유어 promoted → 정식 모듈 = slug 로 통일 (registry 가 픽업한
    후로는 정적과 동일)

Date partition: KST date (~/.tradingagents/screener_cache/{slug}_YYYY-
MM-DD.json). 자정 KST 만료 (다음 날 cache miss).

Override: `/screener <slug> fresh` 가 force_fresh=True 로 캐시 bypass.

Audit: cache hit 도 ~/.tradingagents/screener_cache_audit.jsonl 에
event='cache_hit' 으로 기록 — /usage 같은 cost 카드와 별개 카운트.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.screener import ScreenerResult

log = logging.getLogger("bot.screener_cache")

_CACHE_DIR = os.path.expanduser("~/.tradingagents/screener_cache")
_AUDIT_LOG = os.path.expanduser("~/.tradingagents/screener_cache_audit.jsonl")
# 파일명 사용 가능한 문자만 허용 (path traversal 차단). slug 는 정규 alphanumeric
# + _ + hyphen, 자유어 freetext key 는 sha256 hex.
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,80}$")


def _kst_today_iso() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _cache_path(cache_key: str, date_iso: str) -> Optional[str]:
    if not _SAFE_KEY_RE.match(cache_key):
        log.warning("screener_cache: rejected unsafe key %r", cache_key)
        return None
    return os.path.join(_CACHE_DIR, f"{cache_key}_{date_iso}.json")


def get(cache_key: str) -> Optional["ScreenerResult"]:
    """Today's KST cache lookup. Returns ScreenerResult on hit, None on
    miss / corrupt / unsafe key.
    """
    if not cache_key:
        return None
    path = _cache_path(cache_key, _kst_today_iso())
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        from bot.screener import ScreenerResult  # delayed import
        result = ScreenerResult(
            domain=obj["domain"],
            raw_output=obj["raw_output"],
            validated_tickers=list(obj.get("validated_tickers") or []),
            rejected_tickers=list(obj.get("rejected_tickers") or []),
            elapsed_sec=float(obj.get("elapsed_sec") or 0.0),
            cost_krw=float(obj.get("cost_krw") or 0.0),
            was_cached=True,
        )
        _audit("cache_hit", cache_key, obj.get("domain"), path)
        return result
    except Exception as exc:
        log.warning("screener_cache: load failed for %s: %s", cache_key, exc)
        return None


def save(cache_key: str, result: "ScreenerResult") -> None:
    """Persist ScreenerResult under today's KST date partition. Best-
    effort — any IO failure logged + ignored. Skips when cache_key empty
    or when result was itself loaded from cache (was_cached=True) to
    avoid re-writing the same blob."""
    if not cache_key:
        return
    if getattr(result, "was_cached", False):
        return
    path = _cache_path(cache_key, _kst_today_iso())
    if not path:
        return
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "domain": result.domain,
                "raw_output": result.raw_output,
                "validated_tickers": list(result.validated_tickers),
                "rejected_tickers": list(result.rejected_tickers),
                "elapsed_sec": float(result.elapsed_sec),
                "cost_krw": float(result.cost_krw),
            }, f, ensure_ascii=False, indent=2)
        _audit("cache_save", cache_key, result.domain, path)
    except Exception as exc:
        log.warning("screener_cache: save failed for %s: %s", cache_key, exc)


def _audit(event: str, cache_key: str, domain: Optional[str], path: str) -> None:
    """Append a tiny audit line — used to count cache hits/saves separately
    from /usage cost log. Best-effort."""
    try:
        os.makedirs(os.path.dirname(_AUDIT_LOG), exist_ok=True)
        now = datetime.now(timezone(timedelta(hours=9)))
        with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": now.isoformat(timespec="seconds"),
                "date": now.date().isoformat(),
                "event": event,
                "cache_key": cache_key,
                "domain": domain,
                "path": path,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass
