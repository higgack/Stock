"""Generic date-grouped archive page renderer.

Drop-in mirror of NOAH Daily Byte Archive (bot/dashboard.py
_render_daily_byte_page) with the NOAH-specific bits (Telegram, cost
tracking, infographic PNG embed, delete API endpoint binding) removed.

Renders any list of date-stamped records into a self-contained HTML page
with the same UX as the source:
    - 📆 월 collapse (이번 달 펼침 / 과거 달 접힘)
        - 📅 일 collapse (오늘 펼침 / 그 외 접힘)
            - card (search-indexed body)
    - 본문 검색바 + 스니펫 하이라이트
    - 다크/라이트 테마 토글

Public API:
    render_archive_page(runs, *, title, subtitle, field_map=...) -> str
    FieldMap          — names of fields on each `run` dict
    Stat              — optional top-of-page stat tile

Inputs:
    runs        list of dicts. Each dict represents one archive entry.
                Required fields (named by FieldMap):
                  - date   : 'YYYY-MM-DD' (for grouping)
                  - ts     : ISO timestamp (for HH:MM display)
                  - body   : str — Telegram-style HTML body (<b>/<i>).
                Optional fields (any name, just pass key in FieldMap):
                  - title  : str — card heading override (default 'date')
                  - cost   : numeric — appears in card meta
                  - elapsed: numeric (seconds) — appears in card meta
                  - kind   : str — badge label (e.g. 'daily' / 'weekly')

Usage (from caller's regenerate_*_index() function):
    from daily_byte_archive_template import render_archive_page, FieldMap
    html = render_archive_page(
        runs=load_runs(),
        title="My Archive",
        subtitle="Daily briefing archive · since 2026-06",
        field_map=FieldMap(
            date="_date", ts="ts", body="body",
            cost="cost_krw", elapsed="elapsed_sec", kind="kind",
        ),
    )
    (ARCHIVE_ROOT / "my_archive.html").write_text(html, encoding="utf-8")

The caller owns:
  • where the page lives (ARCHIVE_ROOT)
  • when to regenerate (file-watcher / cron / manual)
  • the data source (jsonl / sqlite / API)
  • Telegram push / cost logging / delete endpoint (out of scope here)

Python 3.10+ (PEP-604 union syntax). No external deps.
"""
from __future__ import annotations

import html as _html
import json as _json
import re as _re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from trade.archive_assets import ARCHIVE_CSS as _RAW_CSS, ARCHIVE_JS as _RAW_JS

# The handoff assets bundle a full <!DOCTYPE>…</head><body> preamble (and a
# trailing </body></html>) so they self-test standalone. render_archive_page
# below emits its OWN clean <!doctype><head><title>…</head><body> wrapper, so
# strip the bundled scaffolding here to avoid a double-wrapped document — the
# CSS rules + JS logic stay byte-verbatim (only the doc tags are dropped).
ARCHIVE_CSS = _re.search(r"<script>.*</style>", _RAW_CSS, _re.DOTALL).group(0)
ARCHIVE_JS = _re.sub(r"</body></html>\s*$", "", _RAW_JS).strip()


# ─────────────────────────────────────────────────────────────────────────
# Public types
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class FieldMap:
    """Map your record dict's field names → the renderer's slot names.

    Required:
        date    field that holds 'YYYY-MM-DD' (group key)
        ts      field that holds ISO timestamp (for clock display)
        body    field that holds the HTML/text body (Telegram-style is fine)

    Optional (set to None to omit that piece):
        title   field for card heading (defaults to date string)
        cost    numeric field shown in card meta as '₩{val:,.1f}'
        elapsed numeric (seconds) shown in card meta as '{val:.0f}s'
        kind    string field for a small badge before the title
    """
    date: str = "_date"
    ts: str = "ts"
    body: str = "body"
    title: str | None = None
    cost: str | None = "cost_krw"
    elapsed: str | None = "elapsed_sec"
    kind: str | None = "kind"


@dataclass
class Stat:
    """A top-of-page stat tile. value is rendered as-is (you format)."""
    value: str
    label: str


# ─────────────────────────────────────────────────────────────────────────
# Internals
# ─────────────────────────────────────────────────────────────────────────

_KST = timezone(timedelta(hours=9))


def _month_kr(ym: str) -> str:
    """'YYYY-MM' → 'YYYY년 M월' (Korean). Fallback to raw on parse error."""
    try:
        y, m = ym.split("-")[:2]
        return f"{int(y)}년 {int(m)}월"
    except Exception:
        return ym


def _today_kst_iso() -> str:
    return datetime.now(_KST).date().isoformat()


def _clean_body(body: str) -> str:
    """Strip markdown horizontal rules and collapse blank lines.

    The source (Telegram-style HTML) sometimes carries '---' / '***'
    separator lines that look ugly in HTML cards. Same strip that NOAH
    applies render-side so legacy archives self-heal."""
    body = _re.sub(r"(?m)^[^\w\n<]*[-*_]{2,}[^\w\n<]*$", "", body or "")
    body = _re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def _make_card_lines(body: str) -> list[dict]:
    """Per-line snippet index for the search bar. Strips HTML tags so the
    searchable text doesn't include '<b>' noise. Caps at 200 lines / 300
    chars per line to keep the JSON data-attribute reasonable."""
    plain = _re.sub(r"<[^>]+>", "", body)
    out: list[dict] = []
    for ln in plain.splitlines():
        s = ln.strip()
        if len(s) >= 3:
            out.append({"sec": "brief", "txt": s[:300]})
    return out[:200]


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def render_archive_page(
    runs: list[dict],
    *,
    title: str,
    subtitle: str,
    field_map: FieldMap | None = None,
    nav_html: str = "",
    stats: list[Stat] | None = None,
    empty_message: str = "아직 기록이 없습니다.",
    delete_api: str | None = None,
    id_field: str = "file",
) -> str:
    """Render the full archive HTML page.

    Args:
        runs           list of record dicts. Order doesn't matter — rendered
                       newest-first by `date` field.
        title          page H1 (e.g. "My Archive — Title").
        subtitle       page sub-line (e.g. "Daily brief · KST 09:00").
        field_map      see FieldMap. Defaults to NOAH Daily Byte names.
        nav_html       optional HTML to put in the nav strip ('<a>...</a>'
                       links separated by ' · '). Empty = nav strip omitted.
        stats          optional list of Stat tiles shown above search bar.
        empty_message  shown when `runs` is empty.

    Returns the full HTML string (one document, self-contained).
    """
    fm = field_map or FieldMap()
    runs = list(runs)

    # Group by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_date[r.get(fm.date, "")].append(r)

    parts: list[str] = ["<!doctype html>\n<html lang='ko'><head>\n",
                        "<meta charset='utf-8'>\n",
                        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n",
                        f"<title>{_html.escape(title)}</title>\n",
                        ARCHIVE_CSS,
                        "</head><body>\n",
                        '<div class="wrap">\n']

    if nav_html:
        parts.append(f'<div class="nav">{nav_html}</div>\n')
    parts.append(f"<h1>{_html.escape(title)}</h1>\n")
    parts.append(f'<p class="sub">{_html.escape(subtitle)}</p>\n')

    if stats:
        parts.append('<div class="stats">')
        for s in stats:
            parts.append(
                f'<div class="stat"><div class="stat-v">{_html.escape(s.value)}</div>'
                f'<div class="stat-l">{_html.escape(s.label)}</div></div>'
            )
        parts.append('</div>\n')

    parts.append(
        '<div class="search-bar">'
        '<input id="scr-search" type="text" '
        'placeholder="본문 검색 (제목 / 본문 / 섹터 / 키워드)" '
        'autocomplete="off" spellcheck="false">'
        '<button id="scr-clear" type="button" title="검색 초기화">초기화</button>'
        '</div>\n'
    )
    total = len(runs)
    parts.append(f'<p id="scr-status" class="status-line">총 {total}건</p>\n')
    parts.append('<div id="scr-snippets" class="snippets" style="display:none"></div>\n')
    parts.append('<div id="scr-empty" class="empty" style="display:none">검색 결과가 없습니다.</div>\n')

    if not runs:
        parts.append(f'<div class="empty">{_html.escape(empty_message)}</div>\n')
        parts.append('</div>\n</body></html>')
        return "".join(parts)

    # Month → day → card nest
    _today = _today_kst_iso()
    _this_month = _today[:7]
    month_counts: dict[str, int] = defaultdict(int)
    for d in by_date:
        month_counts[(d or "")[:7]] += len(by_date[d])

    months: dict[str, list[str]] = defaultdict(list)
    for date in sorted(by_date.keys(), reverse=True):
        months[(date or "")[:7]].append(date)

    for month in sorted(months.keys(), reverse=True):
        m_open = " open" if month == _this_month else ""
        parts.append(
            f'<details class="month"{m_open}>'
            f'<summary class="month-head">'
            f'<span>📆 {_html.escape(_month_kr(month))}</span>'
            f'<span class="count">{month_counts[month]}건</span>'
            f'</summary>'
            f'<div class="month-body">'
        )
        for date in months[month]:
            d_open = " open" if date == _today else ""
            day_count = len(by_date[date])
            parts.append(
                f'<details class="day"{d_open}>'
                f'<summary class="day-head">'
                f'<span>📅 {_html.escape(date)}</span>'
                f'<span class="count">{day_count}건</span>'
                f'</summary>'
                f'<div class="day-body">'
            )
            for r in by_date[date]:
                # Extract HH:MM from ISO 'YYYY-MM-DDTHH:MM:SS+09:00'
                raw_ts = str(r.get(fm.ts) or "")
                ts_clock = raw_ts.split("T", 1)[1][:5] if "T" in raw_ts else ""
                ts_html = _html.escape(ts_clock)

                # Build card meta line
                meta_parts: list[str] = []
                if ts_html:
                    meta_parts.append(f"⏱ {ts_html}")
                if fm.cost and r.get(fm.cost) is not None:
                    try:
                        meta_parts.append(f"₩{float(r[fm.cost]):,.1f}")
                    except (TypeError, ValueError):
                        pass
                if fm.elapsed and r.get(fm.elapsed) is not None:
                    try:
                        meta_parts.append(f"{float(r[fm.elapsed]):.0f}s")
                    except (TypeError, ValueError):
                        pass
                meta = " · ".join(meta_parts)

                # Card title (kind badge + title field, fallback to date)
                title_bits: list[str] = []
                if fm.kind and r.get(fm.kind):
                    title_bits.append(_html.escape(str(r[fm.kind])))
                if fm.title and r.get(fm.title):
                    title_bits.append(_html.escape(str(r[fm.title])))
                else:
                    title_bits.append(_html.escape(date))
                card_title = " · ".join(title_bits)

                body = _clean_body(str(r.get(fm.body) or ""))
                plain = _re.sub(r"<[^>]+>", "", body)
                search_attr = _html.escape(plain.lower()[:6000])
                lines_attr = _html.escape(
                    _json.dumps(_make_card_lines(body), ensure_ascii=False)
                )

                card_default_open = (
                    date == _today and day_count == 1
                )
                card_open_attr = " open" if card_default_open else ""
                # Stable id for snippet-click target
                card_id = f"card-{_re.sub(r'[^a-zA-Z0-9]', '_', date + '-' + str(ts_clock or 'x'))}"

                del_btn = ""
                if delete_api:
                    _did = _html.escape(str(r.get(id_field) or ""))
                    del_btn = (f'<button class="card-del" type="button" '
                               f'data-del="{_did}" title="삭제" '
                               'style="margin-left:auto;background:none;border:none;'
                               'cursor:pointer;font-size:15px;padding:0 4px">🗑️</button>')
                parts.append(
                    f'<details class="card"{card_open_attr} id="{card_id}" '
                    f'data-date="{_html.escape(date)}" '
                    f'data-search="{search_attr}" '
                    f'data-lines="{lines_attr}" '
                    f'data-default-open="{"true" if card_default_open else "false"}">'
                    f'<summary class="card-h">'
                    f'<span class="card-toggle">▸</span>'
                    f'<span class="domain">{card_title}</span>'
                    f'<span class="meta">{meta}</span>'
                    f'{del_btn}'
                    f'</summary>'
                    f'<div class="card-body">'
                    f'<div class="analysis-sec">'
                    f'<div class="analysis-b" data-section="brief">{body}</div>'
                    f'</div>'
                    f'</div>'
                    f'</details>'
                )
            parts.append('</div></details>')  # close day
        parts.append('</div></details>')  # close month

    parts.append('</div>\n')  # close .wrap
    parts.append(ARCHIVE_JS)
    if delete_api:
        # 🗑️ 삭제 — trade 프록시는 GET 만 포워드(POST 미지원)라 GET 으로(다른 trade
        # API 동일). 인증·토큰 뒤라 안전. 성공 시 카드 DOM 제거.
        _api_js = _json.dumps(delete_api)
        parts.append(
            "<script>(function(){var API=" + _api_js + ";"
            "document.addEventListener('click',function(e){"
            "var b=e.target.closest('.card-del');if(!b)return;"
            "e.preventDefault();e.stopPropagation();"
            "if(!confirm('이 항목을 삭제할까요?'))return;"
            "fetch(API+'?file='+encodeURIComponent(b.dataset.del),"
            "{cache:'no-store',credentials:'include'})"
            ".then(function(r){return r.json();})"
            ".then(function(d){if(d&&d.ok){var c=b.closest('.card');if(c)c.remove();}"
            "else{alert('삭제 실패: '+((d&&d.error)||''));}})"
            ".catch(function(){alert('네트워크 오류');});});})();</script>")
    parts.append('\n</body></html>')
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Self-test / demo
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = [
        {"_date": "2026-06-02", "ts": "2026-06-02T09:00:00+09:00",
         "body": "오늘 브리프 본문 — 첫 번째 라인.\n둘째 라인.",
         "cost_krw": 53.4, "elapsed_sec": 106, "kind": "📊 Daily"},
        {"_date": "2026-05-30", "ts": "2026-05-30T22:00:00+09:00",
         "body": "주간 종합 — 로테이션 분석.",
         "cost_krw": 70.0, "elapsed_sec": 200, "kind": "📅 Weekly"},
    ]
    html = render_archive_page(
        runs=sample,
        title="📊 Demo Archive",
        subtitle="generic date-grouped archive · 데모 데이터",
        stats=[
            Stat(value=str(len(sample)), label="총 항목"),
            Stat(value=f"₩{sum(r['cost_krw'] for r in sample):,.0f}",
                 label="누적 비용"),
        ],
    )
    out = "/tmp/demo_archive.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out} ({len(html)} bytes)")
    # Balance check
    n_open = len(_re.findall(r"<details\b", html))
    n_close = len(_re.findall(r"</details>", html))
    print(f"<details> balance: open={n_open} close={n_close} "
          f"{'OK' if n_open == n_close else 'IMBALANCED'}")
