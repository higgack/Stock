"""유료 AI 보고서 아카이브 — 기업 보고서·전체(월) 보고서의 **AI(유료) 실행 결과**를
그 시점 모습 그대로 동결 저장 + 대시보드 색인 (사용자 2026-06-18 '돈내고 분석한건
옆에 대시보드 연결해서 아카이브되게').

무료(₩0) 실행은 기록하지 않는다 — 비용이 든 산출물만 보존이 목적. 기업/전체 두
보고서가 같은 `render_llm(data)->(html, meta)` 모양을 공유하므로 단일 아카이브로
합친다(universal — kind 필드로 구분).

데이터 흐름:
  company_report.render_llm / period_report.render_llm (meta.used=True)
    → record(kind, title, html_body, summary, cost_krw):
        1) 그 시점 보고서 HTML 전체를 self-contained 페이지로
           ~/.trade/dashboard/report_archive_pages/{ts}_{slug}.html 에 동결
        2) 색인 1건(검색본문+링크)을 report_archive.jsonl 에 append
    → regenerate(): jsonl → 날짜 그룹 색인 페이지(report_archive.html)
  대시보드 '🤖 AI 보고서 아카이브' 링크 → 색인 → 카드 '전체 보기' → 동결 페이지.

dashboard_server 가 ~/.trade/ 를 정적 서빙하므로 dashboard/ 밑 파일은 자동 노출.
색인 HTML 소유는 렌더(이 모듈) — fetch 무관, 호출 즉시 갱신(append+regenerate).
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade.archive_template import FieldMap, Stat, render_archive_page

log = logging.getLogger("trade.report_archive")

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
ARCHIVE_JSONL = _DATA_DIR / "report_archive.jsonl"
ARCHIVE_HTML = _DATA_DIR / "dashboard" / "report_archive.html"
SNAP_DIR = _DATA_DIR / "dashboard" / "report_archive_pages"   # 동결 페이지 디렉토리

_FM = FieldMap(date="_date", ts="ts", body="body", title="title",
               cost="cost_krw", elapsed=None, kind="kind")

_SLUG_RE = re.compile(r"[^a-zA-Z0-9가-힣]+")

# 동결 페이지 — 보고서 HTML은 이미 인라인 스타일(다크 카드)이라 최소 wrapper만.
_PAGE_CSS = (
    "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "'Apple SD Gothic Neo','Noto Sans KR',sans-serif;background:#0d1117;"
    "color:#e6edf3;margin:0;padding:18px;line-height:1.55}"
    "a{color:#58a6ff}.rb-wrap{max-width:920px;margin:0 auto}"
    ".rb-back{font-size:13px;margin-bottom:14px;color:#9aa0aa}"
)


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


def _append(rec: dict) -> None:
    ARCHIVE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _standalone(title: str, inner_html: str) -> str:
    """보고서 HTML을 감싼 독립 페이지(back-link 포함). 받는 화면용."""
    e = _html.escape
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)}</title><style>{_PAGE_CSS}</style></head><body>"
        "<div class='rb-wrap'>"
        # '대시보드' = trade 대시보드(상위 디렉토리). index.html 은 NOAH 프록시가
        # _OUR_ROOT_PAGES 로 가로채 NOAH 종목분석으로 보내므로 '../'(디렉토리)로
        # 트레이드 루트를 직접 가리킨다(사용자 2026-06-18 '한국수출입으로 가야지').
        "<div class='rb-back'><a href='../report_archive.html'>← AI 보고서 아카이브</a>"
        " · <a href='../'>대시보드</a></div>"
        f"{inner_html}</div></body></html>"
    )


def record(*, kind: str, title: str, html_body: str, summary: str,
           cost_krw: float | None = None, tg: str = "",
           now: datetime | None = None) -> str | None:
    """유료 AI 보고서 1건 동결 + 색인 기록. 반환=동결 파일 상대경로(실패 시 None).
    호출측이 best-effort(try/except)로 감싼다 — 아카이브 실패가 보고서 응답을 막지
    않게. summary 는 검색용 평문(렌더 시 .analysis-b white-space:pre-wrap). tg =
    텔레그램 전송용 전체 본문(저장 → 아카이브에서 재과금 없이 채널 전송, 사용자
    2026-06-18 '이미 나온 내용을 텔레그램으로')."""
    now = now or datetime.now(_KST)
    ts_compact = now.strftime("%Y%m%d_%H%M%S")
    slug = _SLUG_RE.sub("-", (title or "report")).strip("-")[:40] or "report"
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    # 같은 초에 2건 실행되면 파일명이 충돌해 한쪽이 덮어써지고 색인이 같은 파일을
    # 두 카드가 가리켜 삭제가 꼬인다(사용자 16:03 2건). 존재 시 카운터로 유니크화.
    fname = f"{ts_compact}_{slug}.html"
    _i = 1
    while (SNAP_DIR / fname).exists():
        fname = f"{ts_compact}_{slug}_{_i}.html"
        _i += 1
    (SNAP_DIR / fname).write_text(_standalone(title, html_body), encoding="utf-8")
    rel = f"report_archive_pages/{fname}"
    link = (f'<a href="{rel}" style="display:inline-block;margin-top:8px;'
            'padding:8px 14px;background:#2f81f7;color:#fff;border-radius:8px;'
            'text-decoration:none;font-weight:600">🔗 이 AI 보고서 전체 보기 →</a>')
    body = (summary + "\n\n" + link) if summary else link
    rec = {
        "_date": now.date().isoformat(),
        "ts": now.isoformat(timespec="seconds"),
        "title": title,
        "kind": kind,
        "body": body,
        "file": rel,
    }
    if cost_krw:
        rec["cost_krw"] = cost_krw
    if tg:
        rec["tg"] = tg
    _append(rec)
    return rel


def send_to_channel(file: str) -> dict:
    """아카이브된(이미 생성·과금된) 보고서를 텔레그램 채널로 전송 — 재계산/재과금
    없음(사용자 2026-06-18). 저장된 tg 전체를 4096 단위 분할 전송. {ok, sent}."""
    file = (file or "").strip()
    if not _FILE_RE.match(file):
        return {"ok": False, "error": "잘못된 파일"}
    rec = next((r for r in load_runs() if r.get("file") == file), None)
    if not rec:
        return {"ok": False, "error": "기록 없음"}
    tg = (rec.get("tg") or "").strip()
    if not tg:
        # 이 기능(tg 저장) 이전 기록은 색인 본문(summary+요약)으로 폴백 — '🔗 전체
        # 보기' 링크(<a>)만 제거하면 평문이라 텔레그램 전송 가능(사용자 2026-06-18).
        tg = re.sub(r"<a\b[^>]*>.*?</a>", "", rec.get("body") or "",
                    flags=re.DOTALL).strip()
    if not tg:
        return {"ok": False, "error": "전송 본문 없음"}
    ids = [int(x) for x in (os.environ.get("TRADE_CHANNEL_CHAT_IDS") or "").split(",")
           if x.strip()]
    if not ids:
        return {"ok": False, "error": "TRADE_CHANNEL_CHAT_IDS 미설정"}
    sent = 0
    try:
        from trade.scripts import customs_alert
        for cid in ids:
            if customs_alert.send_long(cid, tg) > 0:
                sent += 1
    except Exception as exc:
        log.warning("report_archive send_to_channel: %s", exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": sent > 0, "sent": sent}


def regenerate(out_path: Path | None = None) -> Path:
    """jsonl → 날짜 그룹 색인 HTML. 빈 상태도 안전."""
    runs = load_runs()
    total_cost = sum(float(r.get("cost_krw") or 0) for r in runs)
    stats = [Stat(value=str(len(runs)), label="AI 보고서")]
    if total_cost:
        stats.append(Stat(value=f"₩{total_cost:,.0f}", label="누적 비용"))
    html = render_archive_page(
        runs=runs,
        title="🤖 AI 보고서 아카이브",
        subtitle="유료 AI(Gemini) 실행 결과 보존 · 기업/전체(월) 보고서 · 카드 → "
                 "'전체 보기'로 그 시점 분석을 그대로 다시 봅니다 · 월/일 접기·검색",
        field_map=_FM,
        # './' = trade 대시보드 루트(/trade/). 'index.html' 은 NOAH 프록시가
        # 가로채 NOAH 로 보내므로 디렉토리로 직접 가리킨다(사용자 2026-06-18).
        nav_html='<a href="./">← 대시보드</a>',
        stats=stats,
        empty_message="아직 저장된 AI 보고서가 없습니다. '🏢 기업 보고서' 또는 "
                      "'🗂️ 전체 보고서'의 'AI 보고서 (유료)'를 실행하면 여기에 "
                      "자동 적립됩니다.",
        delete_api="api/report_archive_delete",   # 🗑️ 카드별 삭제(사용자 2026-06-18)
        send_api="api/report_archive_send",        # 📤 저장본 채널 전송(재과금 없음)
        id_field="file",
    )
    out = out_path or ARCHIVE_HTML
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    _migrate_frozen_navs()
    return out


_FILE_RE = re.compile(r"^report_archive_pages/[\w.\-]+\.html$")


def delete(file: str) -> bool:
    """색인 record + 동결 페이지 삭제 (file=record['file'] 상대경로). path traversal
    가드(화이트리스트 정규식). 해당 record 없거나 형식 위반이면 False."""
    file = (file or "").strip()
    if not _FILE_RE.match(file):
        return False
    runs = load_runs()
    kept = [r for r in runs if r.get("file") != file]
    if len(kept) == len(runs):
        return False
    try:
        ARCHIVE_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with ARCHIVE_JSONL.open("w", encoding="utf-8") as f:
            for r in kept:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:
        return False
    try:
        p = SNAP_DIR / Path(file).name
        if p.exists():
            p.unlink()
    except Exception:
        pass
    regenerate()
    return True


def _migrate_frozen_navs() -> None:
    """기존 동결 페이지의 '대시보드' back-link 이 NOAH 로 새던 것 일괄 정정 — #505
    이전 생성분은 '../index.html'(NOAH 프록시 가로챔)이라 '../'(trade 루트)로 치환.
    idempotent·best-effort(파일 몇 개라 저렴)."""
    try:
        for p in SNAP_DIR.glob("*.html"):
            try:
                t = p.read_text(encoding="utf-8")
            except Exception:
                continue
            if "../index.html" in t:
                p.write_text(t.replace("href='../index.html'", "href='../'")
                             .replace('href="../index.html"', 'href="../"'),
                             encoding="utf-8")
    except Exception:
        pass


def ensure_exists() -> None:
    """대시보드 링크가 404 안 나도록 색인 HTML 없으면 생성(빈 상태 OK).
    best-effort — 실패해도 메인 렌더를 막지 않는다."""
    try:
        if not ARCHIVE_HTML.exists():
            regenerate()
    except Exception:
        pass
