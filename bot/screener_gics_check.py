"""Quarterly GICS / sector classification change check (사용자 정책 2026-05-29).

Runs 4x/year via systemd timer (3월·6월·9월·12월 5일 09:00 KST):
  1. Pro call (gemini-2.5-pro + web search grounding) — surveys the
     last 3 months for: (a) S&P Dow Jones GICS / MSCI Industry
     Classification 의 sector/industry 추가·변경·재분류 announcement,
     (b) emerging industry trend 중 공식 분류 진입 후보.
  2. Cross-checks the result against the current screener_themes
     registry — filters out anything already represented.
  3. Sends a Telegram alert to the configured channel listing the
     net-new candidates with a one-line rationale + suggested layer
     (L1 trend / L2 sector / L3 industry) for each.
  4. Logs the raw Pro response for audit (사용자가 환각 검증 시 참조).

The user reviews the alert and decides whether to ship a new module —
nothing is auto-added. False-positive tolerance is the whole point;
catching a real new industry early (예: 2026-Q3 GICS 가 Quantum
Computing 을 정식 sub-industry 로 등재) outweighs the occasional
hallucinated suggestion. 사용자 직접 확인 정책 2026-05-29.

Cost: ~1 Pro call per quarter ≈ ₩300/quarter ≈ ₩1.2K/year.

Run manually for testing:
    python3 -m bot.screener_gics_check [--dry-run]

systemd:
    deploy/screener-gics-check.{service,timer}
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("bot.screener_gics_check")

_KST = timezone(timedelta(hours=9))
_AUDIT_LOG = Path.home() / ".tradingagents" / "gics_check_audit.jsonl"


def _kst_now() -> datetime:
    return datetime.now(_KST)


def _prev_quarter_window() -> tuple[str, str]:
    """Return (start_iso, end_iso) for the 3-month look-back ending today."""
    end = _kst_now()
    start = end - timedelta(days=92)  # ~3 months
    return start.date().isoformat(), end.date().isoformat()


def _build_check_prompt(known_slugs: list[str], known_domains: list[str]) -> str:
    """Construct the Pro prompt — survey GICS + emerging industry trend."""
    start, end = _prev_quarter_window()
    today = _kst_now().date().isoformat()
    # Cap the known-domains list so the prompt stays reasonable (~65 entries
    # current; will keep growing). 한글 + 영문 모두 cite — fuzzy matching
    # later filters duplicates.
    known_list = "\n".join(f"  - {s} ({d})" for s, d in zip(known_slugs, known_domains))

    return f"""오늘 (KST): {today}
조사 윈도: {start} ~ {end} (지난 3개월)

너는 institutional buy-side 의 sector taxonomy 담당자다. 임무: 위 윈도
동안 (a) S&P Dow Jones GICS / MSCI Industry Classification 공식 변경 +
(b) 글로벌 emerging industry trend 중 공식 분류 진입 후보를 식별.

## (a) GICS / MSCI 공식 변경 (필수 web search)
검색 키워드: "S&P Dow Jones GICS reclassification {start[:4]}", "MSCI
Industry Classification Standard updates", "SPDR sector ETF
reconstitution", "GICS structure changes". 분기 review 결과 + 임시 발표
모두 포함. 발견된 변경:
  • Sector 추가/제거 (예: 2018 Communication Services 신설)
  • Sub-industry 추가/제거
  • 기업 reclassification (예: 특정 large-cap 이 다른 sector 로 이동)
  • Effective date + 발표 source

## (b) Emerging industry trend (web search + 시장 관찰)
공식 GICS 진입 전이지만 buy-side 가 별도 lens 로 다뤄야 할 신규 trend.
판단 기준:
  • 시가총액 합산 $50B+ 또는 분기 매출 합산 $5B+ 산업
  • 최소 5개 이상 글로벌 publicly listed pure-play
  • 명확한 binding constraint (특정 supply chain / 정책 / 기술 cycle)
  • 기존 GICS 분류 어디에도 자연 fit 안 됨
후보 예 (참고용, 검증 의무): Quantum Computing · Brain-computer interface
· Synthetic Biology · Carbon Capture Storage · Stratospheric Cube-sat
· Vertical Farming · Lab-grown Meat · CRISPR Diagnostic 등.

## 우리 시스템의 기존 도메인 (총 {len(known_slugs)}개)
이미 cover 중인 도메인 (slug · 한국어 명):
{known_list}

## 출력 (JSON 만, 다른 prose 금지)

```json
{{
  "window_start": "{start}",
  "window_end": "{end}",
  "official_gics_changes": [
    {{
      "type": "sector_addition|industry_addition|reclassification",
      "name": "추가/변경된 sector 또는 industry 의 정식 영문 명",
      "korean_name": "한국어",
      "parent_sector": "(industry 인 경우) 소속 sector",
      "effective_date": "YYYY-MM-DD or YYYY-Qn",
      "source": "발표 source + URL or publication date",
      "summary": "1-2 문장 (한국어)",
      "already_covered": true/false,
      "matching_existing_slug": "기존 slug 또는 null",
      "suggested_layer": "L2_SECTOR|L3_INDUSTRY|null",
      "suggested_slug": "신규 도메인 slug 후보 (snake_case)"
    }}
  ],
  "emerging_trends": [
    {{
      "name": "신규 trend 영문 명",
      "korean_name": "한국어",
      "rationale": "binding constraint + 시총/매출 규모 + 대표 종목 3-5",
      "estimated_market_cap_usd_billion": 50,
      "key_tickers": ["TICKER1", "TICKER2", "TICKER3"],
      "already_covered": true/false,
      "matching_existing_slug": "기존 slug 또는 null",
      "suggested_layer": "L1_TREND|L3_INDUSTRY",
      "suggested_slug": "신규 도메인 slug 후보"
    }}
  ],
  "summary": "1-2 문장 — 이번 분기 핵심 finding (한국어)"
}}
```

규칙:
- official_gics_changes 가 없으면 빈 배열 []. 환각 금지.
- emerging_trends 는 5개 이내 (high-conviction 만).
- already_covered=true 인 경우도 reporting 에 포함 — matching_existing_
  slug 명시 (사용자가 기존 도메인 강화 필요한지 판단).
- 모든 source 는 publication date + URL 명시. cutoff (2024-Q2) 데이터
  단독 사용 금지 — web search 결과 우선.
- 시제 규율: 'YYYY 효력' 표기 시 effective_date 가 오늘 이후이면 '예정',
  오늘 이전이면 web search 로 실제 시행 여부 verify 의무.
"""


def _call_pro(api_key: str, prompt: str) -> tuple[str, int, int]:
    """Mirror of bot.screener._call_pro — Gemini Pro + web search grounding.
    Returns (text, prompt_tokens, output_tokens)."""
    from google import genai
    client = genai.Client(api_key=api_key)
    grounding_config = None
    try:
        from google.genai import types as _genai_types
        grounding_config = _genai_types.GenerateContentConfig(
            tools=[_genai_types.Tool(
                google_search=_genai_types.GoogleSearch()
            )],
            temperature=0.3,
        )
    except Exception as exc:
        log.warning("genai types unavailable: %s — falling back to plain", exc)

    if grounding_config is not None:
        resp = client.models.generate_content(
            model="gemini-2.5-pro", contents=prompt, config=grounding_config,
        )
    else:
        resp = client.models.generate_content(
            model="gemini-2.5-pro", contents=prompt,
        )
    text = (resp.text or "").strip()
    usage = getattr(resp, "usage_metadata", None)
    pt = getattr(usage, "prompt_token_count", 0) or 0
    ot = getattr(usage, "candidates_token_count", 0) or 0
    return text, pt, ot


def _extract_json(text: str) -> dict | None:
    """Pull the JSON block from Pro response. Tolerates ```json fenced
    + bare {...}. Returns None on parse failure."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        m = re.search(r"(\{[\s\S]*\})", text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        log.warning("GICS check JSON parse failed: %s", exc)
        return None


def _filter_known(items: list[dict], known_slugs: set[str], known_domains: set[str]) -> list[dict]:
    """Drop items that look like duplicates of existing domains. Pro is
    asked to mark already_covered=true itself, but we double-check by
    fuzzy-matching the name against known domain strings (lowercased,
    spaces normalized)."""
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9가-힣]+", "", (s or "").lower())
    known_norms = {_norm(d) for d in known_domains} | {_norm(s) for s in known_slugs}
    out = []
    for it in items:
        if it.get("already_covered"):
            continue
        name_norm = _norm(it.get("name") or "")
        kr_norm = _norm(it.get("korean_name") or "")
        if name_norm in known_norms or kr_norm in known_norms:
            continue
        out.append(it)
    return out


def _format_telegram_alert(report: dict, n_new_gics: int, n_new_trends: int) -> str:
    """Render the Telegram HTML message — concise summary + per-item
    bullets so the user can scan + react quickly."""
    def _esc(s: str) -> str:
        import html
        return html.escape(str(s or ""))

    win_start = _esc(report.get("window_start", "?"))
    win_end = _esc(report.get("window_end", "?"))
    summary = _esc(report.get("summary", ""))

    lines = [
        "📊 <b>분기 GICS / 신규 산업 점검</b>",
        f"<i>윈도: {win_start} ~ {win_end}</i>",
        "",
        f"{summary}" if summary else "",
        "",
    ]

    if n_new_gics:
        lines.append("━━━ <b>공식 GICS 변경</b> ━━━")
        lines.append("")
        for it in report.get("official_gics_changes", []):
            if it.get("already_covered"):
                continue
            name = _esc(it.get("name") or "?")
            kr = _esc(it.get("korean_name") or "")
            parent = _esc(it.get("parent_sector") or "")
            eff = _esc(it.get("effective_date") or "")
            src = _esc(it.get("source") or "")
            slug = _esc(it.get("suggested_slug") or "?")
            layer = _esc(it.get("suggested_layer") or "?")
            kind = _esc(it.get("type") or "")
            lines.append(
                f"• <b>{name}</b> ({kr})\n"
                f"   유형: {kind} · 상위: {parent} · 효력: {eff}\n"
                f"   제안: <code>{slug}</code> ({layer})\n"
                f"   출처: {src}"
            )
            lines.append("")
    else:
        lines.append("<i>공식 GICS 변경 없음.</i>")
        lines.append("")

    if n_new_trends:
        lines.append("━━━ <b>신규 industry trend</b> ━━━")
        lines.append("")
        for it in report.get("emerging_trends", []):
            if it.get("already_covered"):
                continue
            name = _esc(it.get("name") or "?")
            kr = _esc(it.get("korean_name") or "")
            mc = it.get("estimated_market_cap_usd_billion") or 0
            tickers = ", ".join(it.get("key_tickers") or [])[:200]
            rationale = _esc(it.get("rationale") or "")[:300]
            slug = _esc(it.get("suggested_slug") or "?")
            layer = _esc(it.get("suggested_layer") or "?")
            lines.append(
                f"• <b>{name}</b> ({kr})\n"
                f"   시총 ~${mc}B · 종목: {_esc(tickers)}\n"
                f"   제안: <code>{slug}</code> ({layer})\n"
                f"   근거: {rationale}"
            )
            lines.append("")
    else:
        lines.append("<i>신규 trend candidate 없음.</i>")
        lines.append("")

    lines += [
        "━━━",
        "<i>사용자 직접 확인 후 모듈 add 결정. 환각 의심 시 raw 응답</i>",
        f"<i>참조: <code>{_AUDIT_LOG}</code>.</i>",
    ]
    return "\n".join(p for p in lines if p is not None)


def _send_telegram(token: str, chat_id: int, text: str) -> bool:
    """Synchronous Telegram sendMessage via HTTP API. Chunked to fit
    the 4096-char cap; each chunk parsed as HTML."""
    base = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = _chunk_for_telegram(text)
    ok = True
    for chunk in chunks:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id),
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(base, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status >= 300:
                    log.warning("telegram sendMessage HTTP %s", resp.status)
                    ok = False
        except Exception as exc:
            log.exception("telegram sendMessage failed: %s", exc)
            ok = False
        time.sleep(0.5)
    return ok


def _chunk_for_telegram(text: str, cap: int = 3900) -> list[str]:
    """Split on blank lines to stay under Telegram's 4096-UTF-16 cap."""
    parts = text.split("\n\n")
    chunks: list[str] = []
    cur = ""
    for p in parts:
        candidate = (cur + "\n\n" + p) if cur else p
        if len(candidate.encode("utf-16-le")) // 2 > cap:
            if cur:
                chunks.append(cur)
            cur = p
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def _write_audit(report: dict, raw: str, cost_krw: float) -> None:
    """Append the raw + parsed report to the audit JSONL so the user
    can review the underlying Pro response when sanity-checking the
    Telegram alert."""
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _kst_now().isoformat(timespec="seconds"),
            "report": report,
            "raw_response": raw[:8000],  # cap to avoid log bloat
            "cost_krw": cost_krw,
        }
        with _AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("audit entry appended to %s", _AUDIT_LOG)
    except Exception as exc:
        log.warning("audit write failed: %s", exc)


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )
    parser = argparse.ArgumentParser(description="Quarterly GICS check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip Telegram send + audit write")
    args = parser.parse_args()

    # Load .env (mirrors bot.dashboard_server pattern)
    try:
        from dotenv import load_dotenv
        repo_root = Path(__file__).resolve().parent.parent
        env_path = repo_root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        log.error("GOOGLE_API_KEY missing")
        return 1
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_ids_raw = os.environ.get("CHANNEL_CHAT_IDS", "")
    chat_ids = [int(x) for x in chat_ids_raw.split(",") if x.strip()]
    if not args.dry_run and (not tg_token or not chat_ids):
        log.error("TELEGRAM_BOT_TOKEN or CHANNEL_CHAT_IDS missing (use --dry-run to skip send)")
        return 1

    from bot.screener_themes import list_domains
    known = list_domains()
    known_slugs = [d["slug"] for d in known]
    known_domains = [d["domain"] for d in known]
    log.info("checking GICS changes against %d known domains", len(known))

    prompt = _build_check_prompt(known_slugs, known_domains)
    try:
        text, pt, ot = _call_pro(api_key, prompt)
    except Exception as exc:
        log.exception("Pro call failed: %s", exc)
        return 2

    # Cost computation — Gemini 2.5 Pro pricing (mirrors screener.py)
    cost_usd = (pt * 1.25 + ot * 10.00) / 1e6
    cost_krw = cost_usd * 1330.0
    log.info("Pro call OK · in=%d out=%d · ₩%.2f", pt, ot, cost_krw)

    report = _extract_json(text)
    if report is None:
        log.error("Pro returned non-JSON response — sending raw text to Telegram for manual review")
        if not args.dry_run:
            _send_telegram(
                tg_token, chat_ids[0],
                "⚠️ GICS check Pro 응답 JSON parse 실패. raw 응답:\n\n"
                + text[:3500],
            )
        return 3

    # Filter already-known items
    known_slug_set = set(known_slugs)
    known_domain_set = set(known_domains)
    report["official_gics_changes"] = _filter_known(
        report.get("official_gics_changes") or [],
        known_slug_set, known_domain_set,
    )
    report["emerging_trends"] = _filter_known(
        report.get("emerging_trends") or [],
        known_slug_set, known_domain_set,
    )

    n_new_gics = len([
        it for it in report["official_gics_changes"]
        if not it.get("already_covered")
    ])
    n_new_trends = len([
        it for it in report["emerging_trends"]
        if not it.get("already_covered")
    ])

    msg = _format_telegram_alert(report, n_new_gics, n_new_trends)

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — Telegram message:")
        print("=" * 60)
        print(msg)
        print()
        print(f"audit (would write): {_AUDIT_LOG}")
        print(f"cost: ₩{cost_krw:.2f}")
        return 0

    _write_audit(report, text, cost_krw)
    # Regen dashboard page so the new candidate(s) appear immediately
    # (not only at midnight). Audit log is the source of truth; this
    # is a re-render of the same data. Errors swallowed — the alert
    # already shipped, page can catch up at next periodic refresh.
    try:
        from bot.dashboard import regenerate_gics_candidates_index
        regenerate_gics_candidates_index()
    except Exception as exc:
        log.warning("gics_candidates.html regen failed: %s", exc)

    sent = _send_telegram(tg_token, chat_ids[0], msg)
    if not sent:
        log.error("telegram alert send failed (check journal)")
        return 4
    log.info("alert sent · %d new GICS · %d new trends", n_new_gics, n_new_trends)
    return 0


if __name__ == "__main__":
    sys.exit(main())
