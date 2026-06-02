"""산업트렌드 월별 아카이브 — 데이터 변동 시점의 산업 요약 + 🔍 LLM 신호를
date-grouped 아카이브 페이지로 보관(NOAH Daily Byte UX 미러, 핸드오프
archive_template 사용).

데이터 흐름:
  refresh_signals (데이터 변동 틱, ~월1회)
    → record_snapshot(conn, cards): 그 달 산업 상태 1건을
      ~/.trade/industry_archive.jsonl 에 append/replace(확정월 키 idempotent)
    → regenerate(): jsonl → archive_template.render_archive_page →
      ~/.trade/dashboard/industry_archive.html (dashboard_server가 그대로 서빙)
  산업트렌드 탭의 '🗄 월별 아카이브' 링크 → /dashboard/industry_archive.html
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade import industry
from trade.archive_template import FieldMap, Stat, render_archive_page

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
ARCHIVE_JSONL = _DATA_DIR / "industry_archive.jsonl"
ARCHIVE_HTML = _DATA_DIR / "dashboard" / "industry_archive.html"

_FM = FieldMap(date="_date", ts="ts", body="body", title="title",
               cost="cost_krw", elapsed=None, kind="kind")

_GROUP_EMOJI = {"초고성장/강세": "🚀", "턴어라운드 후보": "🔄", "부진/재하락": "🔻"}


def _pct(v, suf="%") -> str:
    return "—" if v is None else f"{v:+.0f}{suf}"


def build_snapshot_body(by_ind, by_imp, cards) -> tuple[str, str]:
    """(최신 확정월, 텔레그램풍 HTML 본문) — 분류 요약 + 수입 급증 + 🔍 신호."""
    series = industry.industry_series(by_ind) if by_ind else {}
    imp_series = industry.industry_series(by_imp) if by_imp else {}
    latest = ""
    for pts in series.values():
        if pts and pts[-1]["ym"] > latest:
            latest = pts[-1]["ym"]

    buckets: dict[str, list] = {}
    for ind, pts in series.items():
        if not pts:
            continue
        buckets.setdefault(industry.classify(pts), []).append((ind, pts[-1]))

    blocks: list[str] = []
    for label in ("초고성장/강세", "턴어라운드 후보", "부진/재하락"):
        items = buckets.get(label) or []
        if not items:
            continue
        items.sort(key=lambda t: (t[1].get("yoy") if t[1].get("yoy") is not None
                                  else -9e9), reverse=True)
        chips = " · ".join(f"{i} {_pct(p.get('yoy'))}" for i, p in items[:10])
        blocks.append(f"<b>{_GROUP_EMOJI.get(label, '')} {label}</b>\n{chips}")

    imp = [(i, ip[-1].get("yoy")) for i, ip in imp_series.items()
           if ip and ip[-1].get("yoy") is not None]
    imp = sorted([t for t in imp if t[1] >= 20.0],
                 key=lambda t: t[1], reverse=True)[:6]
    if imp:
        blocks.append("<b>📥 수입 급증(생산·투자 선행)</b>\n"
                      + " · ".join(f"{i} {_pct(v)}" for i, v in imp))

    if cards:
        sig = "\n".join(f"• <b>{c.get('title', '')}</b> — {c.get('body', '')}"
                        for c in cards if c.get("title") and c.get("body"))
        if sig:
            blocks.append(f"<b>🔍 데이터가 말하는 추가 신호</b>\n{sig}")

    return latest, "\n\n".join(blocks)


def load_runs() -> list[dict]:
    try:
        lines = ARCHIVE_JSONL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _upsert(rec: dict) -> None:
    """확정월 key로 idempotent — 같은 확정월 재기록 시 교체(중복 카드 방지)."""
    runs = [r for r in load_runs() if r.get("key") != rec["key"]]
    runs.append(rec)
    runs.sort(key=lambda r: r.get("ts", ""))
    ARCHIVE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE_JSONL.open("w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def record_snapshot(conn, cards, *, cost_krw=None, now=None) -> str | None:
    """현재 저장 상태로 스냅샷 1건 기록. 반환=확정월 키(데이터 없으면 None)."""
    by_ind = industry.load_stored(conn)
    if not by_ind:
        return None
    by_imp = industry.load_stored_imports(conn)
    latest, body = build_snapshot_body(by_ind, by_imp, cards)
    if not latest:
        return None
    now = now or datetime.now(_KST)
    rec = {
        "key": latest,                          # 확정월 = dedup 키
        "_date": now.date().isoformat(),        # 기록일(KST)
        "ts": now.isoformat(timespec="seconds"),
        "title": f"{latest} 확정 기준 산업트렌드",
        "kind": "📊 월간",
        "body": body,
    }
    if cost_krw:
        rec["cost_krw"] = cost_krw
    _upsert(rec)
    return latest


def regenerate(out_path: Path | None = None) -> Path:
    """jsonl → archive HTML. 빈 상태도 안전(빈 안내 페이지)."""
    runs = load_runs()
    months = len({(r.get("key") or "")[:7] for r in runs if r.get("key")})
    html = render_archive_page(
        runs=runs,
        title="📈 산업트렌드 월별 아카이브",
        subtitle="관세청 확정치 기준 · 데이터 변동(새 확정월) 시 자동 기록 · "
                 "월/일 접기·본문 검색·테마 토글",
        field_map=_FM,
        nav_html='<a href="index.html">← 대시보드</a>',
        stats=[Stat(value=str(len(runs)), label="기록 수"),
               Stat(value=str(months), label="확정월")],
        empty_message="아직 기록이 없습니다. 다음 확정월(매월 ~15일) 유입 시 "
                      "자동 기록됩니다.",
    )
    out = out_path or ARCHIVE_HTML
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def ensure_exists() -> None:
    """대시보드 링크가 404 안 나도록 아카이브 HTML 없으면 생성(빈 상태 OK).
    best-effort — 실패해도 메인 렌더를 막지 않는다."""
    try:
        if not ARCHIVE_HTML.exists():
            regenerate()
    except Exception:
        pass
