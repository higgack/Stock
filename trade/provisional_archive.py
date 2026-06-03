"""잠정 속보 타임라인 아카이브 — 잠정치만 따로 시점별로 적립.

산업트렌드 월별 아카이브(industry_archive)는 '확정월' 동결 기록이라 ~1달
선행하는 잠정과 시점이 안 맞는다(운영자 결정 A안). 그래서 잠정은 별도
타임라인으로 쌓는다: 잠정 창(1~10일 → 1~20일 → 마감)이 새로 발표돼 '최신
창'이 바뀔 때마다 그 시점 헤드라인 숫자를 스냅샷 1건으로 적립.

쓰임: 6/1에 본 5월 마감 잠정(전체 수출 877억$ 등)을 그대로 남겨두면, 6/15
5월 확정이 산업트렌드에 들어왔을 때 '그때 잠정 ↔ 확정'을 시점 안 엉키게
대조할 수 있다(특히 ⚠️ 수출 절대액 과대 검증, capex 선행 적중 여부).

데이터 흐름:
  fetch_provisional (실제 fetch 시) → record(signals):
    key=(ym, window)로 idempotent append/replace (같은 창 재기록 = 현행화
    반영, 최초 기록일·시각 보존). 본문 = 그 시점 헤드라인 4지표 검색 텍스트.
  dashboard.main() → regenerate(): jsonl → 날짜 그룹 색인(provisional_archive.html)
  🟢 잠정 속보 박스 '🗄 잠정 타임라인' → 색인.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade import customs, customs_provisional as prov
from trade.archive_template import FieldMap, Stat, render_archive_page

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
PROV_ARCHIVE_JSONL = _DATA_DIR / "provisional_archive.jsonl"
PROV_ARCHIVE_HTML = _DATA_DIR / "dashboard" / "provisional_archive.html"

_FM = FieldMap(date="_date", ts="ts", body="body", title="title",
               cost=None, elapsed=None, kind="kind")


def load_runs() -> list[dict]:
    try:
        lines = PROV_ARCHIVE_JSONL.read_text(encoding="utf-8").splitlines()
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
    """(ym, window) key로 idempotent — 같은 창 재기록 시 교체(현행화 반영)."""
    runs = [r for r in load_runs() if r.get("key") != rec["key"]]
    runs.append(rec)
    runs.sort(key=lambda r: r.get("ts", ""))
    PROV_ARCHIVE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with PROV_ARCHIVE_JSONL.open("w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _metric_line(label: str, usd, yoy, *, lead: bool = False,
                 warn: bool = False) -> str:
    tag = " ⚡" if lead else ""
    if warn:
        tag += " ⚠️"
    return f"{label}{tag} {customs.fmt_usd(usd)} (YoY {customs.fmt_pct(yoy)})"


def _summary_text(signals: dict) -> str:
    """색인 카드 본문 = 그 시점 헤드라인 4지표(검색 텍스트). 박스와 동일한
    선정: 전체 수출/수입 + ⚡반도체제조용장비 수입(capex 선행) + 반도체 수출."""
    imp = signals.get("imp_item")
    exp = signals.get("exp_item")
    lines: list[str] = []
    if exp:
        lines.append(_metric_line("전체 수출", exp.get("total_usd"),
                                  exp.get("total_yoy"), warn=True))
    if imp:
        lines.append(_metric_line("전체 수입", imp.get("total_usd"),
                                  imp.get("total_yoy")))
    capex = prov._find(imp, "반도체제조용장비", "제조용장비")
    if capex:
        lines.append(_metric_line("반도체제조용장비 수입(capex 선행)",
                                  capex.get("usd"), capex.get("yoy"), lead=True))
    semi = prov._find(exp, "반도체")
    if semi:
        lines.append(_metric_line("반도체 수출", semi.get("usd"),
                                  semi.get("yoy"), warn=True))
    return "\n".join(lines)


def record(signals: dict, *, rows_by_kind: dict | None = None,
           now=None) -> str | None:
    """그 시점 잠정 헤드라인 + 🔟 10일 모멘텀(품목·국가 40)을 (ym, window) 키로
    타임라인에 적립. 같은 창 재기록 = 현행화 반영(값 갱신), 최초 기록일·시각은
    보존. rows_by_kind를 주면 모멘텀 4그룹 테이블이 본문에 동봉돼 그 시점의
    전체 시계열까지 동결된다. 반환=key. best-effort — 예외는 삼킴."""
    try:
        ref = (signals.get("exp_item") or signals.get("imp_item")
               or signals.get("exp_cnty") or signals.get("imp_cnty"))
        if not ref:
            return None
        ym = ref.get("ym")
        window = ref.get("window") or ref.get("decile") or ""
        if not ym:
            return None
        body = _summary_text(signals)
        if not body:
            return None
        # 🔟 10일 모멘텀 4그룹 테이블 동봉(인라인 스타일이라 dashboard CSS
        # 없는 아카이브 페이지에서도 색·표식 정상). 운영자 요청: 아카이브에
        # 모멘텀까지 다 들어가야 됨.
        if rows_by_kind:
            mom_html = prov.momentum_archive_html(rows_by_kind)
            if mom_html:
                body = body + "\n\n" + mom_html
        key = f"{ym} · {window}"
        now = now or datetime.now(_KST)
        prev = next((r for r in load_runs() if r.get("key") == key), None)
        _date = (prev or {}).get("_date") or now.date().isoformat()
        _ts = (prev or {}).get("ts") or now.isoformat(timespec="seconds")
        _upsert({
            "key": key,
            "_date": _date,
            "ts": _ts,
            "title": f"🟢 잠정 {ym} · {window}",
            "kind": "🟢 잠정 스냅샷",
            "body": body,
        })
        return key
    except Exception:
        return None


def regenerate(out_path: Path | None = None) -> Path:
    """jsonl → 날짜 그룹 색인 HTML. 빈 상태도 안전."""
    runs = load_runs()
    months = len({(r.get("key") or "")[:7] for r in runs if r.get("key")})
    html = render_archive_page(
        runs=runs,
        title="🟢 잠정 속보 타임라인",
        subtitle="잠정 창(1~10일→1~20일→마감)이 발표될 때마다 그 시점 숫자를 "
                 "적립 · 확정(익월 ~15일)이 들어오면 '그때 잠정 ↔ 확정' 대조용 · "
                 "단위 억$ · 수출 절대액 ⚠️ 과대 가능 · 월/일 접기·검색",
        field_map=_FM,
        nav_html='<a href="index.html">← 대시보드</a>',
        stats=[Stat(value=str(len(runs)), label="잠정 스냅샷"),
               Stat(value=str(months), label="개월")],
        empty_message="아직 적립된 잠정 스냅샷이 없습니다. 다음 잠정 발표"
                      "(매월 11·21일, 익월 1일)마다 자동 적립됩니다.",
    )
    out = out_path or PROV_ARCHIVE_HTML
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def ensure_exists() -> None:
    """대시보드 링크가 404 안 나도록 색인 HTML 없으면 생성(빈 상태 OK).
    best-effort — 실패해도 메인 렌더를 막지 않는다."""
    try:
        if not PROV_ARCHIVE_HTML.exists():
            regenerate()
    except Exception:
        pass
