"""Bottleneck Screener — free-text domain Phase 0 generator.

User policy 2026-05-29: `/screener <자유어>` (alias miss 자동 fallback)
허용 — Pro 가 즉석에서 theme dict 생성 → 기존 5-phase 파이프라인 통과.
65 정적 도메인 (L1 6 + L2 11 + L3 48) 으로 cover 안 되는 niche (carbon
nanotube · 우주 발사체 · 양자 통신 cryptography 등) 를 무제한 확장.

Design (6 decisions, user-ratified 2026-05-29):
  1. Trigger = alias miss 자동 fallback (UX 매끄러움; "도메인 자동 생성
     중..." 1줄 안내).
  2. Vagueness guard = Phase 0 prompt 가 '3+ binding layer 안 나오면
     reason 으로 reject' 명시. 시도 비용 1회 발생.
  3. 기존 도메인 중복 = registry 의 aliases + domain + binding terms
     vs input fuzzy match. 0.7+ 유사도면 기존 모듈 라우팅.
  4. 24h 캐시 = `~/.tradingagents/freetext_themes/{hash}.json` (sha256
     hex [:12]). Same input within 24h → Pro Phase 0 skip.
  5. Persistence = 임시 dict 만, 정식 모듈 promote 는 user 수동. 5회+
     반복 호출 시 텔레그램 알림 (promotion 후보).
  6. Audit = `~/.tradingagents/freetext_audit.jsonl` 에 input · hash ·
     generated_dict · phase0_cost · outcome 저장.

Cost: ~₩50-80 / Phase 0 호출 (cache hit 시 ₩0). 5-phase 본 분석은 기존
정적 도메인과 동일 (~₩200-300).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from bot.genai_factory import effective_key as _effective_key
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("bot.screener_freetext")

_FREETEXT_CACHE_DIR = os.path.expanduser("~/.tradingagents/freetext_themes")
_FREETEXT_AUDIT_LOG = os.path.expanduser("~/.tradingagents/freetext_audit.jsonl")
_CACHE_TTL_SEC = 24 * 3600

# USD → KRW 환율 (screener.py 와 동일 값 — 단일 정의 사정 다음 commit 에 통일)
_USD_TO_KRW = 1380.0
# gemini-2.5-pro 가격 (screener.py 와 동일).
_PRO_INPUT_USD_PER_M = 1.25
_PRO_OUTPUT_USD_PER_M = 10.00


# ── Cache helpers ────────────────────────────────────────────────────────


def _cache_key(text: str) -> str:
    """Stable 12-char hex digest. 같은 입력 = 같은 키 (case + whitespace
    normalize). 충돌 가능성 무시 가능 (2^48 keyspace, ~1M 자유어/일 까지)."""
    norm = " ".join(text.strip().lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _cache_path(text: str) -> str:
    return os.path.join(_FREETEXT_CACHE_DIR, f"{_cache_key(text)}.json")


def load_cached_theme(text: str) -> Optional[dict]:
    """24h TTL disk cache lookup. Returns theme dict or None on miss /
    stale / parse error."""
    path = _cache_path(text)
    if not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > _CACHE_TTL_SEC:
            return None
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict) and "domain" in obj:
            return obj
    except Exception as exc:
        log.debug("freetext cache read failed for %s: %s", text, exc)
    return None


def save_cached_theme(text: str, theme: dict) -> None:
    """Persist generated theme dict for 24h re-use. Best-effort."""
    try:
        os.makedirs(_FREETEXT_CACHE_DIR, exist_ok=True)
        with open(_cache_path(text), "w", encoding="utf-8") as f:
            json.dump(theme, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.warning("freetext cache write failed for %s: %s", text, exc)


# ── Audit log ────────────────────────────────────────────────────────────


def _kst_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=9)))


def write_audit(record: dict) -> None:
    """Append one JSONL record to freetext_audit.jsonl. Used for promotion
    candidate detection (5+ uses of same text) + cost reconciliation."""
    try:
        os.makedirs(os.path.dirname(_FREETEXT_AUDIT_LOG), exist_ok=True)
        now = _kst_now()
        record = dict(record)
        record.setdefault("ts", now.isoformat(timespec="seconds"))
        record.setdefault("date", now.date().isoformat())
        with open(_FREETEXT_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("freetext audit write failed: %s", exc)


def count_today_freetext() -> int:
    """Today's KST freetext invocations (cache hits + Phase 0 calls).
    Used for the soft daily cap notice (>5/day = ⚠️ cost reminder)."""
    if not os.path.isfile(_FREETEXT_AUDIT_LOG):
        return 0
    today = _kst_now().date().isoformat()
    n = 0
    try:
        with open(_FREETEXT_AUDIT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("date") == today and rec.get("event") in (
                    "phase0_call", "cache_hit",
                ):
                    n += 1
    except Exception:
        pass
    return n


def count_text_uses(text: str) -> int:
    """How many times this exact text has been used (any date). 5회+
    누적 시 정식 모듈 promotion 후보 알림 트리거."""
    if not os.path.isfile(_FREETEXT_AUDIT_LOG):
        return 0
    key = _cache_key(text)
    n = 0
    try:
        with open(_FREETEXT_AUDIT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("cache_key") == key and rec.get("event") in (
                    "phase0_call", "cache_hit",
                ):
                    n += 1
    except Exception:
        pass
    return n


# ── Auto-promotion to static module ──────────────────────────────────────


_PROMOTE_THRESHOLD = 5  # 누적 호출 횟수 (count_text_uses 단위)
_THEMES_PKG_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "screener_themes",
)


def _slugify(text: str, theme: dict, existing: set[str]) -> str:
    """Pick a valid Python identifier slug for the promoted module.
    Preference order:
      1) First ASCII-only alias that's a clean slug
      2) ASCII fragment of the ``domain`` field
      3) Hash-based fallback ``freetext_<hash[:8]>``

    Result is always unique against ``existing`` (registry slug set) +
    safe to use as a module filename + Python identifier.
    """
    import re

    def _norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
        # Collapse repeats + length cap (Python import-friendly).
        s = re.sub(r"_+", "_", s)
        return s[:30]

    # 1) Aliases — first ASCII-clean one wins.
    for alias in theme.get("aliases") or []:
        s = _norm(str(alias))
        if s and s not in existing and not s[0].isdigit():
            return s

    # 2) Domain ASCII fragment ("탄소나노튜브 (Carbon Nanotube — CNT)" →
    # "carbon nanotube cnt" → "carbon_nanotube_cnt").
    domain = str(theme.get("domain") or "")
    en = re.findall(r"[A-Za-z][A-Za-z0-9\s\-]+", domain)
    if en:
        s = _norm(" ".join(en))
        if s and s not in existing and not s[0].isdigit():
            return s

    # 3) Hash fallback — always unique.
    base = f"freetext_{_cache_key(text)[:8]}"
    s = base
    n = 2
    while s in existing:
        s = f"{base}_{n}"
        n += 1
    return s


def _format_theme_literal(theme: dict) -> str:
    """Pretty-print theme dict as a Python literal suitable for module
    file. Uses json.dumps for stable ordering + readable line wrapping,
    then converts JSON null/true/false → Python None/True/False."""
    body = json.dumps(theme, ensure_ascii=False, indent=4)
    # JSON → Python literal substitutions. Safe because these tokens only
    # appear as bare literals (not inside any string we generate).
    body = body.replace(": null", ": None")
    body = body.replace(": true", ": True")
    body = body.replace(": false", ": False")
    return body


def promote_to_module(text: str, theme: dict) -> Optional[str]:
    """Write the cached theme dict to ``bot/screener_themes/<slug>.py``
    as a permanent module. Returns the chosen slug on success, None if
    the target file already exists (idempotent — same text triggering
    promotion repeatedly is a no-op after first write).

    The new module is picked up by ``_discover()`` on the next bot
    restart (auto-update 1-min cycle will catch it next time the user
    pushes any commit). Until then the freetext cache continues serving
    the same theme so behavior is unchanged.

    Layer stays ``AD_HOC`` to flag it as auto-promoted — user can
    manually edit to L1_TREND/L2_SECTOR/L3_INDUSTRY after review.
    """
    from bot.screener_themes import list_domains

    existing = {d["slug"] for d in list_domains()}
    slug = _slugify(text, theme, existing)

    target = os.path.join(_THEMES_PKG_DIR, f"{slug}.py")
    if os.path.exists(target):
        return None  # already promoted on a prior run

    # Mark for traceability — generated_from + cache_key embedded so the
    # user can locate the audit + cache files when reviewing.
    promote_ts = _kst_now().isoformat(timespec="seconds")
    header = f'''"""Auto-promoted from `/screener <자유어>` — {_PROMOTE_THRESHOLD}+ uses surfaced {promote_ts}.

User input that triggered promotion: "{text}"
Cache key:    {_cache_key(text)}
Cached theme: ~/.tradingagents/freetext_themes/{_cache_key(text)}.json
Audit log:    ~/.tradingagents/freetext_audit.jsonl

Layer = AD_HOC (auto-promoted). Manually edit to L1_TREND / L2_SECTOR /
L3_INDUSTRY after review if appropriate. Same shape as static modules —
``THEME`` dict at module top level, registry auto-discovers next bot
restart.
"""

from __future__ import annotations

THEME = {_format_theme_literal(theme)}
'''
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(header)
        write_audit({
            "event": "promotion",
            "input": text,
            "cache_key": _cache_key(text),
            "slug": slug,
            "target_path": target,
        })
        log.info("freetext promoted to module: %s → %s", text, slug)
        return slug
    except Exception as exc:
        log.exception("freetext promote_to_module failed: %s", exc)
        return None


# ── Existing-domain fuzzy match ──────────────────────────────────────────


def fuzzy_match_existing(text: str) -> Optional[str]:
    """Cheap substring-based 매칭 — 자유어가 사실상 기존 도메인이면 slug
    반환. registry resolve() 의 substring fallback 이 이미 한 단계 시도
    하므로 (resolve 가 None 반환했단 건 거기서도 miss), 여기는 더 lenient
    한 token-set 매칭. 의도: '/screener AI 데이터센터 capa' 같이 modifier
    가 붙어 alias 매칭 실패한 케이스에서 bottleneck 모듈로 라우팅.

    Returns None if no clear single match — caller proceeds to Phase 0.
    """
    from bot.screener_themes import list_domains

    norm = set(text.strip().lower().split())
    if not norm:
        return None

    best_slug: Optional[str] = None
    best_score = 0.0
    for d in list_domains():
        slug = d["slug"]
        # Token bag from slug + domain + aliases. Korean domain names like
        # 'AI Data Center Buildout' get split into single tokens via space.
        bag = " ".join([
            slug, d.get("domain", "") or "",
            *(d.get("aliases") or []),
        ]).lower()
        bag_tokens = set(bag.replace("/", " ").replace("·", " ").split())
        if not bag_tokens:
            continue
        # Jaccard-like coverage — fraction of input tokens that appear in bag.
        overlap = len(norm & bag_tokens)
        if overlap == 0:
            continue
        coverage = overlap / max(1, len(norm))
        if coverage > best_score:
            best_score = coverage
            best_slug = slug

    # Require ≥0.7 coverage AND ≥2 token overlap (single-token match too
    # noisy — '/screener carbon' 이 carbon_capture 로 잘못 매칭하면 곤란).
    if best_score >= 0.7 and len(norm) >= 2:
        return best_slug
    return None


# ── Phase 0 prompt + parse ───────────────────────────────────────────────


_PHASE0_PROMPT = """당신은 글로벌 buy-side 시니어 애널리스트입니다. 사용자가
입력한 자유 텍스트 테마에 대해 Bottleneck Screener 가 분석할 수 있는
**theme dict** 를 JSON 으로 생성합니다.

입력 테마: "{text}"

---

생성 의무:

1. **domain** (str) — 한국어 정식 명칭 (영문 보조). 예: "탄소나노튜브 (Carbon
   Nanotube — CNT)".
2. **layer** (str) — 항상 "AD_HOC" (자유텍스트 도메인 식별자).
3. **horizon** (str) — "12-24 months" 또는 "6-18 months" 등 적절한 catalyst
   윈도.
4. **aliases** (list[str]) — 사용자가 다시 호출할 만한 별칭 5-8개 (한국어 +
   영문 + 약어). 입력 텍스트 자체는 첫 번째 alias 로.
5. **binding_layer_taxonomy** (list[str]) — **최소 3개, 권장 4-6개**의 좁은
   sub-layer. Theory of Constraints 관점 — choke point owner 가 rent 독식
   하는 단계. 각 layer 는 구체 회사명 또는 기술 명시. 예:
     - "CNT 합성 — CVD reactor (Cabot CSWCNT · OCSiAl · LG화학 etc.)"
     - "CNT 분산 (CMP/conductive paste — Showa Denko · 한솔케미칼 etc.)"
   금지: "다양한 산업", "여러 응용 분야" 같은 모호 단어.
6. **catalyst_types** (list[str]) — **최소 5개**의 forward-looking 신호. 정책 /
   capex / 가격 / qual / 경쟁사 stumble / 신규 contract 등 measurable.
7. **regional_concentration** (dict[str, str]) — 각 binding layer 별 지리적
   SPOF + 상장 ticker 매핑. **최소 3개 entry**. 예:
     - "CNT 합성": "US (Cabot CBT), JP (Showa Denko 4004.T), KR (LG화학
       051910.KS), 비상장 (OCSiAl 룩셈부르크)"

---

**REJECT 룰** — 다음 케이스는 JSON 대신 한 줄 reject reason 반환:

  • 입력이 너무 vague ("주식 추천" / "좋은 종목" / "내일 오를 종목" 등) →
    "REJECT: 입력 vague — 구체적 산업/기술/cycle 명시 필요"
  • 실재하지 않는 산업 / 환각 위험 ("외계인 기술주" 등) →
    "REJECT: 실재 industry 식별 불가"
  • binding_layer 가 정직하게 3개 이상 식별 안 되는 niche (지나치게 좁음) →
    "REJECT: 단일 layer 만 식별 — niche 가 너무 좁아 분석 가치 낮음.
    가장 가까운 기존 도메인 사용 권장: <도메인명>"

---

**출력 형식** — JSON 코드 블록 1개만. 추가 prose 금지. example skeleton:

```json
{{
  "domain": "탄소나노튜브 (Carbon Nanotube — CNT)",
  "layer": "AD_HOC",
  "horizon": "12-24 months",
  "aliases": ["carbon nanotube", "cnt", "탄소나노튜브", "swcnt", "mwcnt"],
  "binding_layer_taxonomy": [
    "CNT 합성 reactor (CVD process)",
    "CNT 분산 (전도성 페이스트)",
    "CNT 응용 (배터리 conductive additive)",
    "CNT 응용 (반도체 thermal interface)"
  ],
  "catalyst_types": [
    "주요 배터리 OEM 의 CNT 채택 비율 (전체 cathode CAM)",
    "CNT 가격 ($/kg) 분기 추이",
    "신규 CNT 합성 capa 발표 (kton/yr)",
    "美 IRA 보조금 적격 CNT 공급사 확정",
    "EU 차량용 CNT 표준화 (ISO/IEC) 진행"
  ],
  "regional_concentration": {{
    "CNT 합성": "US (Cabot CBT, GreenTech 비상장), JP (Showa Denko 4004.T)",
    "CNT 분산": "JP (Showa Denko 4004.T), KR (한솔케미칼 014680.KS)",
    "CNT 응용 (배터리)": "KR (LG화학 051910.KS, 동진쎄미켐 005290.KQ)"
  }}
}}
```

REJECT 케이스는 JSON 없이 한 줄만:
```
REJECT: <reason>
```
"""


def _build_phase0_prompt(text: str) -> str:
    return _PHASE0_PROMPT.format(text=text.strip())


def _parse_phase0_response(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """Parse Pro response. Returns (theme_dict, None) on success, or
    (None, reject_reason) when Pro returned a REJECT line / malformed JSON.
    """
    if not raw:
        return None, "Pro 응답이 비어있음"

    # REJECT path — line starts with REJECT (any position in output).
    for line in raw.splitlines():
        s = line.strip().lstrip("`").strip()
        if s.upper().startswith("REJECT:"):
            return None, s

    # Extract JSON code block. Tolerant — looks for ```json ... ``` or
    # falls back to first { ... } match.
    import re
    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    body = m.group(1) if m else None
    if not body:
        # Bare JSON match (greedy) — picks up the largest {...} block.
        m2 = re.search(r"\{.*\}", raw, re.DOTALL)
        body = m2.group(0) if m2 else None
    if not body:
        return None, "Pro 응답에서 JSON 블록을 찾을 수 없음"

    try:
        theme = json.loads(body.strip())
    except Exception as exc:
        return None, f"JSON 파싱 실패: {exc}"

    if not isinstance(theme, dict):
        return None, "JSON 이 dict 형식이 아님"

    # Ensure required keys exist + types correct (delegated to registry
    # _validate for shape consistency with static modules).
    from bot.screener_themes import _validate as _theme_validate
    try:
        _theme_validate("freetext", theme)
    except ValueError as exc:
        return None, f"theme 스키마 위반: {exc}"

    # Schema OK — normalize a few fields.
    theme.setdefault("layer", "AD_HOC")
    theme.setdefault("aliases", [])
    return theme, None


# ── Public API ───────────────────────────────────────────────────────────


def resolve_freetext(text: str) -> tuple[Optional[dict], Optional[str], float, bool]:
    """Main entry — try cache, then fuzzy match, then Phase 0 Pro call.

    Returns (theme_dict, error_str, cost_krw, was_cached):
      • theme_dict — when generation succeeded (theme ready for pipeline)
      • error_str — when REJECT / fuzzy redirect / hard error (UI message)
      • cost_krw — ₩ cost of this resolution (0 on cache hit)
      • was_cached — True if loaded from disk cache (no Pro call)

    Caller (_screener_dispatch) decides what to do with the result:
      - theme returned → run normal 5-phase pipeline
      - error returned → reply with the message, don't run pipeline
    """
    text = (text or "").strip()
    if not text:
        return None, "자유 텍스트 입력 비어있음", 0.0, False

    # 1) Cache hit?
    cached = load_cached_theme(text)
    if cached is not None:
        write_audit({
            "event": "cache_hit",
            "input": text,
            "cache_key": _cache_key(text),
            "domain": cached.get("domain"),
        })
        return cached, None, 0.0, True

    # 2) Fuzzy match against existing domains?
    existing_slug = fuzzy_match_existing(text)
    if existing_slug:
        write_audit({
            "event": "fuzzy_redirect",
            "input": text,
            "cache_key": _cache_key(text),
            "redirect_to": existing_slug,
        })
        return None, f"__REDIRECT__:{existing_slug}", 0.0, False

    # 3) Phase 0 — generate via Pro.
    api_key = _effective_key()
    if not api_key:
        return None, "GOOGLE_API_KEY 미설정 — 자유텍스트 도메인 사용 불가", 0.0, False

    from bot.screener import _call_pro  # delayed import — avoids circular
    prompt = _build_phase0_prompt(text)
    started = time.time()
    try:
        raw_text, pt, ot = _call_pro(api_key, prompt, enable_grounding=True)
    except Exception as exc:
        log.exception("freetext Phase 0 call failed: %s", exc)
        write_audit({
            "event": "phase0_error",
            "input": text,
            "cache_key": _cache_key(text),
            "error": str(exc)[:300],
        })
        return None, f"Phase 0 Pro 호출 실패: {exc}", 0.0, False

    cost_usd = (pt * _PRO_INPUT_USD_PER_M + ot * _PRO_OUTPUT_USD_PER_M) / 1e6
    cost_krw = cost_usd * _USD_TO_KRW

    theme, reject = _parse_phase0_response(raw_text)
    elapsed = time.time() - started

    audit_base = {
        "event": "phase0_call",
        "input": text,
        "cache_key": _cache_key(text),
        "prompt_tok": pt,
        "output_tok": ot,
        "cost_krw": round(cost_krw, 4),
        "elapsed_sec": round(elapsed, 1),
    }
    if theme is None:
        audit_base["outcome"] = "reject"
        audit_base["reject_reason"] = reject
        write_audit(audit_base)
        return None, reject or "Phase 0 출력 파싱 실패", cost_krw, False

    audit_base["outcome"] = "success"
    audit_base["domain"] = theme.get("domain")
    audit_base["binding_layers"] = len(theme.get("binding_layer_taxonomy", []))
    write_audit(audit_base)
    save_cached_theme(text, theme)
    return theme, None, cost_krw, False
