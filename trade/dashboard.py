"""Static HTML dashboard renderer.

Reads from store.db, emits a single self-contained index.html with two
client-side views over the same data:

  품목별 (item view)  : one card per (direction, item, region, country),
                       showing the latest alert with BeOn's graph + table
                       images inline
  회사별 (company view): one section per stock mentioned, with mini-cards
                       linking to the latest alert for each item that
                       references it

Filters (search, direction toggle, status toggle) run client-side over
the embedded ALERTS array, so re-rendering after a filter change is a
single innerHTML pass — no server round-trip.

No external CDN, no JS framework, no build step. The whole renderer is
about 600 lines of Python emitting ~1 MB of HTML for the current
~1000-alert corpus, which loads in under a second on mobile.

CLI:
    python -m trade.dashboard
        → writes ~/.trade/dashboard/index.html
    python -m trade.dashboard --out path.html --media-url /media/
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from dotenv import load_dotenv

from trade.archive_template import SCROLL_RESTORE_JS  # 뒤로가기 스크롤 위치 복원(공용)
from trade.store import latest_per_dedup_key, list_all_alerts, open_db, stats

# 수동 실행(`python -m trade.dashboard`)에서도 TRADE_PUBLIC_HOST 등 .env 값을
# 보도록 로드. systemd refresh는 EnvironmentFile로 주입되지만 수동 렌더는 아님.
load_dotenv()


def render_html(
    db_path: Path | str,
    *,
    media_url_prefix: str = "../",
    eval_miss_path: Path | str | None = None,
    customs_db_path: Path | str | None = None,
    hs_map_path: Path | str | None = None,
    industry_out: Path | str | None = None,
    history_out: Path | str | None = None,
) -> str:
    """Render the dashboard HTML from store.db.

    media_url_prefix is prepended to each stored media path
    (e.g. 'media/2026-05-15/abc.jpg' → '../media/2026-05-15/abc.jpg'),
    so callers can place the HTML wherever and adjust the prefix to
    point at the media tree.

    eval_miss_path optionally points at eval_misses.jsonl (the durable
    backlog of un-parseable BeOn captions written by
    unstored_check.log_eval_misses). When given AND non-empty, the
    header gets a small '🧪 미파싱 백로그' line so the backlog is
    visible at a glance instead of only appearing in the daily ⚠️.
    None / missing file / empty file render nothing — the meta line
    is hidden by the `:empty` CSS rule.
    """
    conn = open_db(db_path)
    try:
        all_alerts = list_all_alerts(conn)
        s = stats(conn)
    finally:
        conn.close()
    # list_all_alerts orders so the first row of each dedup_key block is
    # the 'latest' (newest posted_at + final-wins-tie). The remainder of
    # each block is the history rendered inline in the modal for visual
    # comparison.
    seen: set[str] = set()
    latest_ids: list[int] = []
    for a in all_alerts:
        key = a.get("dedup_key") or ""
        if key not in seen:
            seen.add(key)
            latest_ids.append(a["id"])
    backlog = _load_eval_miss_summary(eval_miss_path)
    customs_rows = _load_customs_summary(customs_db_path, hs_map_path)
    industry_html = _load_industry_html(customs_db_path)
    heatmap_html = _load_heatmap_html(customs_db_path)
    # ⚠️ heatmap_html 인자 누락 = 2026-06-12 12:52 이후 5분 refresh 전부
    # NameError 크래시 → index.html 동결 (순서/잠정/히트맵 전부 미표시의
    # 최종 진범). _build_html 파라미터 누락 회귀는 render 스모크가 가드.
    # 산업트렌드 패널이 무거우면(588 SVG 카드 ~7MB) index.html 인라인 대신 별도
    # 파일로 빼 탭 열 때 lazy fetch — 초기 로딩 11MB→~3MB (사용자 2026-06-16 '느려').
    # industry_out 미지정(아카이브/share·테스트) 시 기존대로 인라인(자체완결 보존).
    industry_src = ""
    industry_csv = ""
    if industry_out is not None and industry_html:
        try:
            # CSV JSON 스크립트(ind-csv-data/mti-csv-summary/mti-csv-monthly)는 메인에
            # 남겨야 csv-btn 이 산업 탭에서 lazy 패널 로드 전에도 찾음(사용자 2026-06-18
            # '산업트렌드 CSV 안 돼' = lazy 분리 시 스크립트가 패널로만 가서 미발견).
            import re as _re
            panel_html = industry_html
            for _cid in ("ind-csv-data", "mti-csv-summary", "mti-csv-monthly"):
                _m = _re.search(
                    r"<script type='application/json' id='" + _cid + r"'>.*?</script>",
                    industry_html, _re.DOTALL)
                if _m:
                    industry_csv += _m.group(0)
                    panel_html = panel_html.replace(_m.group(0), "", 1)
            Path(industry_out).parent.mkdir(parents=True, exist_ok=True)
            Path(industry_out).write_text(panel_html, encoding="utf-8")
            industry_src = Path(industry_out).name      # 같은 디렉토리 상대경로
            industry_html = ""                          # 인라인 제거 → placeholder+data-src
        except Exception:
            industry_src = ""                           # 쓰기 실패 → 인라인 폴백(기능 보존)
            industry_csv = ""
    return _build_html(
        all_alerts, latest_ids, s, media_url_prefix, backlog, customs_rows,
        industry_html, heatmap_html, industry_src=industry_src,
        industry_csv=industry_csv, history_out=history_out,
    )


def _load_industry_html(customs_db_path: Path | str | None) -> str:
    """Server-rendered 산업트렌드 panel from the stored aggregation.

    Reads the precomputed per-industry series (industry_report --store
    writes it) and renders SVG trend cards. Returns '' on any failure or
    when nothing's stored yet, so the tab is simply absent — purely
    additive, never breaks the main render.
    """
    try:
        from trade import customs, industry
        db = customs_db_path if customs_db_path is not None else customs.DEFAULT_DB
        if not Path(db).exists():
            return ""
        from trade import insights, llm_insights
        import json as _json
        from trade import industry_archive, customs_provisional
        with customs.session(db) as conn:
            by_ind = industry.load_stored(conn)
            by_imp = industry.load_stored_imports(conn)
            by_mti = industry.load_mti_stored(conn)
            by_mti_imp = industry.load_mti_imports(conn)
            # 🔍 LLM 추가신호 카드(refresh_signals가 데이터 변동 시 저장)
            try:
                cards = _json.loads(insights.get_state(conn, "llm_insight_cards") or "[]")
            except Exception:
                cards = []
            share_ym = industry_archive.latest_stored_ym(conn)
            # 🟢 잠정 속보(관세청 10일 단위) — fetch_provisional가 저장한
            # 스냅샷만 읽음(렌더는 API 미접속). 비면 '' → motie 배너 폴백.
            # 헤드라인 박스(signals) + 🔟 10일 모멘텀 펼치기(전체 시계열 rows).
            prov_signals = customs_provisional.load_signals(conn)
            prov_rows = customs_provisional.load_rows(conn)
        # 헤드라인 박스 MoM(전월 동순) 즉시 반영 (사용자 2026-06-12 '잠정
        # mom 아직 안보여'): MoM 은 latest_signal 이 산출하는데, 저장 payload
        # 가 MoM 도입(2026-06-12) 전 스냅샷이면 비어 보인다 — fetch_provisional
        # 가 --if-stale(≤6h)로 재fetch 를 미루기 때문(배포 ≠ 화면 반영).
        # 시계열(series_json)에서 렌더타임 재계산 → 재fetch 안 기다리고 배포
        # 직후 표시 + 모멘텀 표(render_momentum)와 동일 소스로 일관. 시계열
        # 없는(구버전) kind 는 저장 payload 유지(폴백).
        for _kind, _rows in prov_rows.items():
            try:
                _sig = customs_provisional.latest_signal(
                    _rows, customs_provisional.LABELS.get(_kind, ()))
            except Exception:
                _sig = None
            if _sig:
                prov_signals[_kind] = _sig
        prov_html = customs_provisional.render_box(
            prov_signals,
            momentum_html=customs_provisional.render_momentum(prov_rows))
        ins_html = llm_insights.render_html(cards if isinstance(cards, list) else [])
        # 🔗 공유: 그 확정월 시점 '스냅샷' 공개 URL(인증 없음)을 클립보드로 복사.
        # 새 확정월이 오면 버튼이 새 URL을 주지만, 이미 보낸 옛 링크는 그 달 동결.
        purl = industry_archive.public_share_url(share_ym)
        if purl:
            from html import escape as _esc
            u = _esc(purl)
            # 모바일(특히 iOS)에선 execCommand 복사 버튼이 막히는 경우가 있어,
            # 길게 누르면 'URL 복사'되는 실제 링크만 둔다(운영자 확인: 이게 확실).
            share_btn = (
                '<span class="ind-share">'
                f'<a class="ind-share-link" href="{u}" target="_blank" rel="noopener">'
                f'🔗 공유 링크 ({_esc(share_ym or "")} 스냅샷) — <b>길게 눌러 복사</b></a>'
                '</span>'
            )
        else:
            share_btn = (
                '<span style="margin-left:16px;color:var(--text-sub);font-size:12px">'
                '🔗 공유 링크: .env에 TRADE_PUBLIC_HOST 설정 필요</span>'
            )
        archive_link = (
            '<div class="ind-archive">'
            '<a href="industry_archive.html">'
            '🗄 월별 아카이브 — 과거 산업트렌드(분류·수입급증·🔍신호) 변천 보기 →</a>'
            f'{share_btn}'
            '</div>'
        )
        # 데이터 기준 영역 구분선 (사용자 2026-06-12 '10일 잠정 ↔ 월간
        # 헷갈려' + '좀 더 선명하게'): 위 = 🟢 잠정 속보(10일 단위), 아래 =
        # 월간 산업트렌드. 강조 pill 라벨(CSS .ind-zone-div 강화).
        zone_div = (
            "<div class='ind-zone-div'><span>📅 여기부터 <b>월간 산업트렌드</b>"
            " — 익월 1일 잠정 적재 → ~15일 확정 정제 · 매일 갱신</span></div>"
        ) if prov_html else ""
        # 산업트렌드 CSV (사용자 2026-06-13 '매트릭스처럼 CSV') — 카드와
        # 같은 집계(by_ind/by_imp)를 롱포맷으로 임베드, csv-btn 이 활성
        # 탭에 따라 소비. (산업, 월, 수출$, 수입$) 전 시계열.
        import json as _json
        _months = sorted({m for d in (by_ind, by_imp or {})
                          for ser in d.values() for m in ser})
        _ind_rows = []
        for _name in sorted(set(by_ind) | set(by_imp or {})):
            _e = by_ind.get(_name, {})
            _i = (by_imp or {}).get(_name, {})
            for _m in _months:
                if _m in _e or _m in _i:
                    _ind_rows.append([_name, _m, _e.get(_m, 0), _i.get(_m, 0)])
        ind_csv = ("<script type='application/json' id='ind-csv-data'>"
                   + _json.dumps(_ind_rows, ensure_ascii=False)
                   .replace("</", "<\\/") + "</script>")
        # 품목(MTI) CSV 2종 (사용자 2026-06-13 '이런것들 엑셀로' — 둘 다):
        # 요약(품목당 1행, 카드 지표) + 월별 원시. csv-btn 이 산업 탭에서 둘
        # 다 내려받음. 채널 관련기업 조인 페어는 렌더당 1회 로드(graceful).
        try:
            from trade.mti_companies import load_channel_pairs
            _ch_pairs = load_channel_pairs()
        except Exception:
            _ch_pairs = []
        try:
            _mti_sum, _mti_mon = industry.mti_export_rows(by_mti, by_mti_imp, _ch_pairs)
        except Exception:
            _mti_sum, _mti_mon = [], []
        mti_csv = (
            "<script type='application/json' id='mti-csv-summary'>"
            + _json.dumps(_mti_sum, ensure_ascii=False).replace("</", "<\\/")
            + "</script><script type='application/json' id='mti-csv-monthly'>"
            + _json.dumps(_mti_mon, ensure_ascii=False).replace("</", "<\\/")
            + "</script>")
        # 잠정 속보 존 구분선 (사용자 2026-06-13) — 월간과 동일 pill 형식,
        # 녹색(속보 톤). 위 = 🟢 10·20일 잠정, 아래 = 📅 월간.
        prov_zone_div = (
            "<div class='ind-zone-div prov'><span>🟢 여기부터 <b>10·20일·"
            "월초 잠정 속보</b> — 관세청 10일 단위 · 11일(1~10)·21일(1~20)·"
            "월초(전월 풀월) 발표 · 확정 대비 ~한 달 선행</span></div>"
        ) if prov_html else ""
        # 순서 (사용자 2026-06-12): 구분선 → 📋 더 빠른 잠정치(motie) →
        # 🗄 월별 아카이브 → 인사이트 → 산업트렌드 본문(motie 제외).
        return (ind_csv + mti_csv + prov_zone_div + prov_html + zone_div + industry.motie_banner() + archive_link
                + ins_html
                + industry.render_industry_html(by_ind, by_imp, by_mti,
                                                by_mti_imp, motie=False))
    except Exception:
        return ""


def _load_heatmap_html(customs_db_path: Path | str | None) -> str:
    """Finviz식 수출입 히트맵 탭 (사용자 2026-06-12) — customs_scan 의
    leaf 스냅샷에서 렌더. 데이터/실패 시 '' → 탭 자체가 안 뜸 (additive)."""
    try:
        from trade import customs, customs_scan, heatmap, industry
        db = customs_db_path if customs_db_path is not None else customs.DEFAULT_DB
        if not Path(db).exists():
            return ""
        with customs.session(db) as conn:
            customs_scan.init_db(conn)
            rows = customs_scan.load_heatmap(conn)
        if not rows:
            return ""
        ref_ym = max(r.get("ref_ym") or "" for r in rows)
        status = industry._month_status_label(ref_ym) if ref_ym else ""
        return heatmap.render_heatmap_html(rows, status_label=status)
    except Exception as exc:
        # silent-swallow 금지 (2026-06-12 '히트맵 하루종일 안 나옴' 추적성)
        # — 데이터는 있는데 렌더 예외로 탭이 사라지는 클래스 가시화.
        import logging
        logging.getLogger("trade-dashboard").warning(
            "heatmap render failed (탭 숨김): %s", exc)
        return ""


def _asof_label(basdt: str) -> str:
    """EOD 기준일(YYYYMMDD) → 한글 요일 1자('목'). 파싱 실패 시 ''. KIS 현재가
    대비 '며칠 종가'인지 한눈에 — data.go.kr EOD는 보통 T+1 지연이라 토요일엔
    목/금 종가가 뜬다."""
    try:
        return "월화수목금토일"[datetime.strptime(basdt[:8], "%Y%m%d").weekday()]
    except (ValueError, TypeError, IndexError):
        return ""


def _stock_quotes_for(payload: list[dict]) -> dict:
    """{종목명: {p: 종가, c: 등락률%, d?: 기준일요일}} — BeOn 관련종목(회사명)을 data.go.kr EOD
    종가에 자동 매칭. 공급자 off(키 없음)/예외/미발견 → 그 종목 생략 또는 {}.

    EOD는 하루 1회만 바뀌므로 TTL을 길게(기본 6h, TRADE_PRICE_EOD_TTL)
    잡아 5분 렌더가 data.go.kr를 반복 호출 안 하게(일일 트래픽 보호). 어떤
    실패든 {} → 칩은 가격 없이 그대로 렌더(표시 전용, 절대 안 깨짐)."""
    try:
        from trade import price_provider
        if not price_provider.provider_active():
            return {}
        raw = []
        for a in payload:
            raw.extend(a.get("stocks") or [])
        names = price_provider.split_names(raw)   # 복합 'A / B 등' 분리
        if not names:
            return {}
        # 렌더는 캐시만 읽어 즉시 끝낸다(fetch=False) — 480종목을 동기 API로
        # 부르면 렌더가 타임아웃돼 일부만 담기던 문제 해결. 실제 시세 호출은
        # 워머(trade.scripts.fetch_quotes)가 백그라운드에서. 캐시 비면 그 종목은
        # 가격 생략(다음 워밍 후 채워짐). TTL은 장중/마감 자동.
        ttl = price_provider.recommended_ttl()
        quotes = price_provider.get_quotes_by_name(names, ttl_s=ttl, fetch=False)
        out = {}
        for nm, q in quotes.items():
            d = {"p": round(q.price), "c": round(q.change_pct, 2)}
            # EOD(지연 종가)만 기준일 요일 라벨 — KIS 현재가(최신)는 라벨 없음.
            if getattr(q, "source", "kis") == "eod":
                lbl = _asof_label(getattr(q, "as_of", ""))
                if lbl:
                    d["d"] = lbl
            out[nm] = d
        return out
    except Exception:
        return {}


def _load_customs_summary(
    customs_db_path: Path | str | None,
    hs_map_path: Path | str | None,
) -> list[dict]:
    """Build the pinned-item comparison rows for the dashboard panel.

    Returns [] (panel hidden) when no DB/pins exist or anything fails —
    the customs feature is purely additive, so a hiccup here must never
    break the main dashboard render. customs_db_path None → use the
    module default; hs_map_path None → hs_map's own default.
    """
    try:
        from trade import customs, hs_map
        pins = hs_map.entries() if hs_map_path is None else hs_map.entries(hs_map_path)
        if not pins:
            return []
        db = customs_db_path if customs_db_path is not None else customs.DEFAULT_DB
        if not Path(db).exists():
            # Pins exist but fetch hasn't created the cache yet — still
            # show the panel so the operator sees '수집 대기' rows.
            return [{"item": it, "hs_code": hs, "has_data": False} for it, hs in pins]
        with customs.session(db) as conn:
            return customs.summary_rows(conn, pins)
    except Exception:
        return []


def _load_eval_miss_summary(
    path: Path | str | None,
) -> dict | None:
    """Read eval_misses.jsonl → {count, oldest_age_days} or None.

    Returns None when the file is missing / empty / unreadable so the
    dashboard renders without the backlog meta line. Malformed JSONL
    lines are skipped (same fault-tolerance as unstored_check), so a
    stray hand-edit can't blank the card.
    """
    if path is None:
        return None
    p = Path(path)
    if not p.exists():
        return None
    # IGNORED 매칭 캡션은 백로그에서 제외 (사용자 2026-06-15) — '주요 기업
    # 수출입 확정치 코멘트' 같은 off-topic 시리즈는 영구 파싱 불가가 정상이라
    # actionable 백로그가 아니다. read-only(파일 미변경)·다음 렌더에 즉시 반영·
    # 향후 IGNORED 추가도 자동 적용. matches_prefix/contains 재사용.
    from trade import ignored as _ignored
    count = 0
    oldest: datetime | None = None
    try:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cap = rec.get("caption") or rec.get("text") or ""
                if _ignored.matches_prefix(cap) or _ignored.matches_contains(cap):
                    continue
                count += 1
                detected = rec.get("detected_at")
                if not detected:
                    continue
                try:
                    if detected.endswith("Z"):
                        dt = datetime.fromisoformat(detected[:-1]).replace(
                            tzinfo=timezone.utc
                        )
                    else:
                        dt = datetime.fromisoformat(detected)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if oldest is None or dt < oldest:
                    oldest = dt
    except OSError:
        return None
    if count == 0:
        return None
    age_days: int | None = None
    if oldest is not None:
        delta = datetime.now(timezone.utc) - oldest
        age_days = max(0, delta.days)
    return {"count": count, "oldest_age_days": age_days}


def _companies_for(stocks: list) -> list:
    """회사별 뷰 그룹핑용 회사명 — split_names로 복합명 분리 + 품목/프로즈/제품
    블록리스트 제외(품목 설명이 가짜 회사로 뜨던 문제). 칩·CSV·검색은 원본 stocks를
    그대로 쓰고 회사 섹션 그룹핑만 이걸 쓴다. split_names 실패 시 원본 폴백."""
    try:
        from trade import price_provider
        return price_provider.split_names(stocks or [])
    except Exception:
        return list(stocks or [])


def _alert_to_payload(a: dict, media_prefix: str) -> dict:
    """Strip the alert down to the fields the client view actually uses.

    Includes dedup_key so the modal can find sibling history alerts
    without a server round-trip, plus all the extra metadata the CSV
    export needs (item_raw, multi region/country JSON, composite parts,
    title_kind, commentary). raw_text and source_* fields are
    intentionally omitted from the payload — they'd inflate it
    materially for little browser-side gain.
    """
    return {
        "id": a["id"],
        "dir": a["direction"],
        "status": a["status"],
        "item": a["item"],
        "item_raw": a.get("item_raw") or a["item"],
        "region": a.get("region") or "",
        "country": a.get("country") or "",
        "regions": a.get("regions") or [],
        "countries": a.get("countries") or [],
        "stocks": a.get("stocks") or [],
        # 회사별 뷰 섹션 그룹핑 전용 — 품목 설명/제품명을 거른 회사명만.
        "companies": _companies_for(a.get("stocks") or []),
        "stocks_meta": a.get("stocks_meta") or {},
        "is_composite": bool(a.get("is_composite")),
        "composite_parts": a.get("composite_parts") or [],
        "title_kind": a.get("title_kind") or "",
        "commentary": a.get("commentary") or "",
        "posted_at": (a.get("posted_at") or "")[:10],
        "ingested_at": (a.get("ingested_at") or "")[:19],
        "period_start": a.get("period_start") or "",
        "period_end": a.get("period_end") or "",
        "period_kind": a.get("period_kind") or "",
        "media": [media_prefix + p for p in (a.get("media_paths") or [])],
        "has_etc": bool(a.get("has_etc")),
        "warnings": a.get("parse_warnings") or [],
        "dedup_key": a.get("dedup_key") or "",
    }


def _cost_line_html() -> str:
    """Compact 'cost/resource' line for the header — every external API is
    free; surfaces customs API-quota headroom + disk usage. Returns '' on
    any failure so a stat hiccup never breaks the render."""
    try:
        from trade import cost
        return escape(cost.format_dashboard_line())
    except Exception:
        return ""


def _customs_panel_html(rows: list[dict]) -> str:
    """관세청 패널 묶음 — 📌 내 핀(수동, 영구) + 자동 발굴(📈 급등률 / 💵 급증액,
    매일 갱신) + 🗄 급변 아카이브(무제한). `rows`는 서버가 미리 로드한 핀 요약;
    자동 섹션은 customs.db에서 직접 읽는다. 핀이 없어도 자동 섹션은 렌더되며,
    모두 비면 '' 반환 → 섹션 생략."""
    from trade.customs import fmt_pct, fmt_usd

    pins_block = ""
    if rows:
        with_data = [r for r in rows if r.get("has_data")]
        latest_ym = ""
        for r in with_data:
            if r.get("year_month", "") > latest_ym:
                latest_ym = r["year_month"]
        ym_label = f" · 최신 {escape(latest_ym)}" if latest_ym else ""

        body = [
            '<table class="customs-table"><thead><tr>'
            '<th>품목</th><th>수출</th><th>전월비</th>'
            '<th>수입</th><th>무역수지</th>'
            '</tr></thead><tbody>'
        ]
        for r in rows:
            item = escape(r.get("item", ""))
            hs = escape(r.get("hs_code", ""))
            if not r.get("has_data"):
                body.append(
                    f'<tr class="nodata"><td>{item} '
                    f'<span class="hs">{hs}</span></td>'
                    f'<td colspan="4">수집 대기</td></tr>'
                )
                continue
            mom = r.get("exp_mom")
            mom_cls = "up" if (mom or 0) > 0 else ("down" if (mom or 0) < 0 else "")
            body.append(
                f'<tr><td>{item} <span class="hs">{hs}</span></td>'
                f'<td class="num">{escape(fmt_usd(r.get("exp_dlr")))}</td>'
                f'<td class="num {mom_cls}">{escape(fmt_pct(mom))}</td>'
                f'<td class="num">{escape(fmt_usd(r.get("imp_dlr")))}</td>'
                f'<td class="num">{escape(fmt_usd(r.get("bal_payments")))}</td></tr>'
            )
        body.append("</tbody></table>")

        pins_block = (
            '<details class="customs-panel">'
            f'<summary>📌 내 핀 ({len(rows)}개{ym_label})</summary>'
            '<div class="customs-note">관세청 월 확정 금액(공식, dataset 15101609) · '
            'BeOn 데이터와 별개 · 수동 핀만</div>'
            + "".join(body)
            + "</details>"
        )

    try:
        from trade import customs_scan
        surge_block = customs_scan.render_surge_html()
    except Exception:
        surge_block = ""

    return pins_block + surge_block


def _build_html(
    alerts: list[dict],
    latest_ids: list[int],
    s: dict,
    media_prefix: str,
    backlog: dict | None = None,
    customs_rows: list[dict] | None = None,
    industry_html: str = "",
    heatmap_html: str = "",
    industry_src: str = "",
    industry_csv: str = "",
    history_out: Path | str | None = None,
) -> str:
    # industry_src(있을 때) = 산업트렌드를 별도 파일로 빼고 탭 열 때 lazy fetch
    # (초기 11MB→~3MB, 사용자 2026-06-16). industry_html 인라인과 양립 — src 우선.
    _has_industry = bool(industry_html) or bool(industry_src)
    full_payload = [_alert_to_payload(a, media_prefix) for a in alerts]
    # ALERTS 인라인 = **최신만**(latest-per-dedup_key), 모달 히스토리(siblings)는
    # 별도 alerts_history.json 으로 빼 모달 첫 클릭 때만 fetch (사용자 2026-06-16
    # '최신만 인라인 + 모달 히스토리 on-demand'). 뷰/검색/CSV 는 전부 isLatest
    # 필터라 최신만 필요 — 리스크는 모달 siblings 한 곳에 격리(fetch 실패 시 최신만
    # 표시되는 graceful degrade). history_out 미지정(아카이브/share·테스트)이면 전체
    # 인라인(back-compat·자체완결, 모달 기존대로). _stock_quotes_for 는 항상 전체
    # 기준 — 히스토리 sibling 카드도 가격칩.
    history_src = ""
    payload = full_payload
    if history_out is not None:
        _latest_set = set(latest_ids)
        _inline = [p for p in full_payload if p["id"] in _latest_set]
        _history = [p for p in full_payload if p["id"] not in _latest_set]
        try:
            Path(history_out).parent.mkdir(parents=True, exist_ok=True)
            Path(history_out).write_text(
                json.dumps(_history, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")
            history_src = Path(history_out).name      # 같은 디렉토리 상대경로
            payload = _inline                         # 인라인 = 최신만
        except Exception:
            payload = full_payload                    # 쓰기 실패 → 전체 인라인 폴백
    payload_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    history_src_json = json.dumps(history_src)
    latest_ids_json = json.dumps(latest_ids, separators=(",", ":"))
    # 관련종목 EOD 시세 — BeOn stocks(회사명)를 data.go.kr 종가에 자동 매칭.
    # 공급자 off/키 없음/예외 → {} (칩은 가격 없이 그대로). 표시 전용. 전체(latest+
    # history) stocks 커버 — 모달 sibling 카드도 가격칩 유지.
    stock_quotes_json = json.dumps(_stock_quotes_for(full_payload),
                                   ensure_ascii=False, separators=(",", ":"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    by_status = s.get("by_status", {})
    by_dir = s.get("by_direction", {})

    # Server-rendered backlog line. unstored_check appends one row per
    # unhandled BeOn caption to eval_misses.jsonl; surfacing the count
    # here keeps the regression backlog visible between daily ⚠️
    # alerts. Empty / missing file → empty div → :empty CSS hides it.
    backlog_inner = ""
    if backlog and backlog.get("count"):
        age = backlog.get("oldest_age_days")
        age_str = f" (가장 오래된 {age}일째)" if isinstance(age, int) else ""
        backlog_inner = (
            f"🧪 미파싱 백로그 <strong>{backlog['count']}건</strong>"
            f"{escape(age_str)}"
            f" — <code>eval_misses.jsonl</code>에 누적"
        )

    head = (
        f'<header><h1>🇰🇷 한국 수출입 데이터</h1>'
        f'<div class="meta">'
        f"갱신 {escape(now)} · "
        f"총 {s.get('total', 0)}건 (최신 {len(latest_ids)}개) · "
        f"수출 {by_dir.get('export', 0)} / 수입 {by_dir.get('import', 0)} · "
        f"잠정 {by_status.get('preliminary', 0)} / 확정 {by_status.get('final', 0)} · "
        f"품목 {s.get('distinct_items', 0)}"
        f"</div>"
        # Three JS-populated lines (status / next / today) plus the
        # server-rendered backlog line. All hidden via :empty when empty
        # so first paint stays clean and the backlog disappears the
        # moment the operator adds a RULE that empties eval_misses.
        f'<div class="meta meta-status" id="meta-status"></div>'
        f'<div class="meta meta-next" id="meta-next"></div>'
        f'<div class="meta meta-today" id="meta-today"></div>'
        f'<div class="meta meta-backlog">{backlog_inner}</div>'
        f'<div class="meta meta-cost">{_cost_line_html()}</div>'
        f'<div class="meta meta-quote">💹 관련종목 시세 네이버 실시간 · 장중 라이브'
        f'(렌더 5분 주기) · 장종료 후엔 당일 종가 고정 · 미제공 종목은 공란</div>'
        f"</header>"
    )

    customs_panel = _customs_panel_html(customs_rows or [])

    # 📡 전체 자동화 플로우 — 데이터 수신→파싱→갱신이 방문 없이 서버 스케줄로 자동
    # (사용자 2026-06-18). 접이식(요약은 항상 보이고 펼치면 전체) — NOAH ℹ️가이드 패턴.
    flow_note = (
        '<details class="flow-note">'
        '<summary>📡 데이터 자동화 플로우 — 방문 안 해도 서버가 자동 갱신 (펼쳐 보기)</summary>'
        '<div class="flow-body">'
        '<b>① 관세청 수출입</b> — 확정치(익월 ~15일 HSK-MTI 집계)가 본체, 잠정 속보'
        '(매월 1·11·21일, 확정보다 ~20일 선행)가 보조. <b>customs-probe가 10분마다</b> '
        '새 통계를 감지하면 산업트렌드·히트맵·월별 원자료·관련기업이 자동 갱신.<br>'
        '<b>② DART 매출구성</b>(회사→제품·비중 매핑) — <b>매월 18일</b> 전수 갱신'
        '(바뀐 보고서만 재파싱; 잠정 몰리는 1일은 회피) + <b>매일 04:30</b> 파서개선 소급'
        ' 드레이너(파서가 좋아지면 기존 데이터까지 며칠에 걸쳐 자동 재파싱·수렴). '
        '영업손익·매출에누리 같은 비제품 회계라인은 제품으로 오매핑하지 않음(빈칸 &gt; 오매핑).<br>'
        '<b>③ 관련기업</b> — BeOn 채널 매트릭스를 실시간 반영(기업 보고서·검색·품목·'
        '레퍼런스북이 같은 소스 공유). <b>별칭 검색</b>(예: MLCC)도 HS 연계로 실제 '
        '품목명(고정식축전기)·수출입 숫자를 함께 표시 — 품목명 클릭 시 재검색. '
        '<b>히트맵 셀 클릭</b>도 그 HS/품목의 관련 상장사·보고서로 연결.<br>'
        '→ <b>모든 표면이 방문 없이 서버 스케줄로 자동 신선</b>하게 유지됩니다. '
        '비용 ₩0(무료 공공 API·LLM 0).'
        '</div></details>'
    )

    return (
        '<!DOCTYPE html>\n'
        '<html lang="ko"><head>'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>한국 수출입 데이터</title>'
        f"<style>{_CSS}</style>"
        '</head><body>'
        + head
        + flow_note
        + customs_panel
        # 🏢 기업 보고서 (사용자 2026-06-17) — 회사 → DART 제품구성 + 관세청 수출입
        # 품목 노출. 무료=데이터(₩0) / AI=Gemini 산문 요약(유료 opt-in). 정적 문자열
        # 영역이라 인라인 <script> 중괄호 안전(f-string 아님).
        + '<section class="report-box">'
        '<div class="rb-row"><span class="rb-title">🏢 기업 보고서</span>'
        '<input type="search" id="rb-q" placeholder="회사·6자리 코드 또는 품목 (예: 삼성전자 / 005930 / 반도체)" autocomplete="off">'
        '<button type="button" id="rb-free" class="rb-btn">무료 보고서</button>'
        '<button type="button" id="rb-llm" class="rb-btn rb-llm">AI 보고서 (유료)</button>'
        '<button type="button" id="rb-chan" class="rb-btn">📤 채널로 전송</button></div>'
        '<div id="rb-result" class="rb-result"></div></section>'
        '<script>(function(){'
        'var q=document.getElementById("rb-q"),res=document.getElementById("rb-result");'
        'function run(mode){var v=(q.value||"").trim();'
        'if(!v){res.innerHTML="<div class=\\"rb-note\\">회사명 또는 6자리 코드를 입력하세요.</div>";return;}'
        'if(mode==="llm"&&!confirm("AI 보고서는 Gemini 비용이 발생합니다. 생성할까요?"))return;'
        'if(mode==="channel"&&!confirm("이 보고서를 텔레그램 채널로 전송할까요?"))return;'
        'res.innerHTML="<div class=\\"rb-note\\">"+(mode==="channel"?"채널 전송 중…":("생성 중… ("+(mode==="llm"?"AI":"무료")+")"))+"</div>";'
        'fetch("api/company_report?q="+encodeURIComponent(v)+"&mode="+mode,{cache:"no-store",credentials:"include"})'
        '.then(function(r){return r.json();})'
        '.then(function(d){'
        'if(d.channel){res.innerHTML="<div class=\\"rb-note\\">"+(d.ok?("✅ 채널 전송 "+(d.sent||0)+"건"):("⚠️ "+(d.error||"전송 실패")))+"</div>";return;}'
        'res.innerHTML=d.ok?d.html:("<div class=\\"rb-note\\">⚠️ "+(d.error||"실패")+"</div>");})'
        '.catch(function(){res.innerHTML="<div class=\\"rb-note\\">⚠️ 네트워크 오류</div>";});}'
        'document.getElementById("rb-free").addEventListener("click",function(){run("free");});'
        'document.getElementById("rb-llm").addEventListener("click",function(){run("llm");});'
        'document.getElementById("rb-chan").addEventListener("click",function(){run("channel");});'
        'q.addEventListener("keydown",function(e){if(e.key==="Enter")run("free");});'
        # 결과 안의 품목명(data-rb-search) 클릭 → 그 실제 품목명으로 재검색 (사용자
        # 2026-06-19 — MLCC 등 별칭으로 들어와도 실제 품목명으로 한 번에 재조회).
        'res.addEventListener("click",function(e){'
        'var t=e.target.closest("[data-rb-search]");if(!t)return;'
        'q.value=t.getAttribute("data-rb-search");q.focus();run("free");'
        'window.scrollTo({top:q.getBoundingClientRect().top+window.scrollY-80,behavior:"smooth"});});'
        # 전역 훅 — 히트맵 셀 클릭 등 외부에서 이 위젯으로 검색을 던지는 단일 진입점
        # (사용자 2026-06-19 '히트맵도 연결'). 검색창 채우고 무료 보고서 실행 + 스크롤.
        'window.rbSearch=function(v){if(!v)return;q.value=v;q.focus();run("free");'
        'window.scrollTo({top:q.getBoundingClientRect().top+window.scrollY-80,behavior:"smooth"});};'
        # 검색어를 지우면(X 버튼·전체삭제) 보고서도 함께 비움 (사용자 2026-06-18).
        'q.addEventListener("input",function(){if(!(q.value||"").trim())res.innerHTML="";});'
        'q.addEventListener("search",function(){if(!(q.value||"").trim())res.innerHTML="";});'
        '})();</script>'
        # 🗂️ 전체 보고서 (사용자 2026-06-17) — 특정 월(기본 최신월) 수출입 전체 집계
        # + 수출↔수입 관계 분석. company_report 위젯 미러(pr-* ids). 무료=데이터(₩0) /
        # AI=Gemini 산문(유료 opt-in). 정적 문자열(인라인 <script> 중괄호 안전).
        + '<section class="report-box">'
        '<div class="rb-row"><span class="rb-title">🗂️ 전체 보고서</span>'
        '<input type="search" id="pr-ym" placeholder="YYYY-MM (비우면 최신월 · 예: 2026-05)" autocomplete="off">'
        '<button type="button" id="pr-free" class="rb-btn">무료 보고서</button>'
        '<button type="button" id="pr-llm" class="rb-btn rb-llm">AI 보고서 (유료)</button>'
        '<button type="button" id="pr-chan" class="rb-btn">📤 채널로 전송</button></div>'
        '<div id="pr-result" class="rb-result"></div></section>'
        '<script>(function(){'
        'var ym=document.getElementById("pr-ym"),res=document.getElementById("pr-result");'
        'function run(mode){var v=(ym.value||"").trim();'
        'if(mode==="llm"&&!confirm("AI 보고서는 Gemini 비용이 발생합니다. 생성할까요?"))return;'
        'if(mode==="channel"&&!confirm("이 보고서를 텔레그램 채널로 전송할까요?"))return;'
        'res.innerHTML="<div class=\\"rb-note\\">"+(mode==="channel"?"채널 전송 중…":("생성 중… ("+(mode==="llm"?"AI":"무료")+")"))+"</div>";'
        'fetch("api/period_report?ym="+encodeURIComponent(v)+"&mode="+mode,{cache:"no-store",credentials:"include"})'
        '.then(function(r){return r.json();})'
        '.then(function(d){'
        'if(d.channel){res.innerHTML="<div class=\\"rb-note\\">"+(d.ok?("✅ 채널 전송 "+(d.sent||0)+"건"):("⚠️ "+(d.error||"전송 실패")))+"</div>";return;}'
        'res.innerHTML=d.ok?d.html:("<div class=\\"rb-note\\">⚠️ "+(d.error||"실패")+"</div>");})'
        '.catch(function(){res.innerHTML="<div class=\\"rb-note\\">⚠️ 네트워크 오류</div>";});}'
        'document.getElementById("pr-free").addEventListener("click",function(){run("free");});'
        'document.getElementById("pr-llm").addEventListener("click",function(){run("llm");});'
        'document.getElementById("pr-chan").addEventListener("click",function(){run("channel");});'
        'ym.addEventListener("keydown",function(e){if(e.key==="Enter")run("free");});'
        # 검색어를 지우면 보고서도 함께 비움 (사용자 2026-06-18).
        'ym.addEventListener("input",function(){if(!(ym.value||"").trim())res.innerHTML="";});'
        'ym.addEventListener("search",function(){if(!(ym.value||"").trim())res.innerHTML="";});'
        '})();</script>'
        # 🤖 유료 AI 보고서 아카이브 링크 (사용자 2026-06-18 '돈내고 분석한건 대시보드에 아카이브').
        + '<div class="report-archive-link"><a href="report_archive.html">'
        '🤖 AI 보고서 아카이브 — 유료로 생성한 기업/전체 보고서 다시 보기 →</a>'
        # 📖 품목 레퍼런스북 (사용자 2026-06-18 '품목↔HS↔관련기업 레퍼런스북').
        ' &nbsp;·&nbsp; <a href="reference.html">'
        '📖 품목 레퍼런스북 — 품목 ↔ HS코드 ↔ 산업 ↔ 관련기업 →</a></div>'
        + '<nav class="tabs">'
        '<button class="tab active" data-tab="items">품목별</button>'
        '<button class="tab" data-tab="companies">회사별</button>'
        '<button class="tab" data-tab="matrix">매트릭스</button>'
        + (f'<button class="tab" data-tab="industry">📈 산업트렌드</button>'
           if _has_industry else '')
        + (f'<button class="tab" data-tab="heatmap">🟩 히트맵</button>'
           if heatmap_html else '')
        + '</nav>'
        '<section class="filters">'
        '<div class="top-row">'
        '<input type="search" id="q" placeholder="검색: 품목 / 회사 / 국가 / 산업 / HS" autocomplete="off">'
        '<button type="button" id="csv-btn" class="csv-btn" title="CSV 내려받기 — 신호알림/히트맵 탭=현재 결과, 산업트렌드 탭=품목 요약+월별 2파일">📥 CSV</button>'
        '</div>'
        '<div class="chips">'
        '<span class="chip-group" data-key="dir">'
        '<button class="chip active" data-val="">전체</button>'
        '<button class="chip" data-val="export">수출</button>'
        '<button class="chip" data-val="import">수입</button>'
        '</span>'
        '<span class="chip-group" data-key="status">'
        '<button class="chip active" data-val="">전체</button>'
        '<button class="chip" data-val="preliminary">잠정</button>'
        '<button class="chip" data-val="final">확정</button>'
        '</span>'
        '<span class="chip-group" data-key="onlynew">'
        '<button class="chip active" data-val="">전체</button>'
        '<button class="chip" data-val="new">🆕 신규</button>'
        '</span>'
        '</div>'
        f'<div class="count"><span id="visible-count">{len(latest_ids)}</span> / {len(latest_ids)} 표시 중</div>'
        '</section>'
        '<main id="items-view" class="view active"></main>'
        '<main id="companies-view" class="view"></main>'
        '<main id="matrix-view" class="view"></main>'
        # 산업트렌드 CSV 데이터 — lazy 분리 시 메인에 남겨 csv-btn 이 항상 찾음(#2 fix).
        + industry_csv
        + (f'<main id="industry-view" class="view" data-src="{industry_src}">'
           '<div class="lazy-load">📈 산업트렌드 불러오는 중…</div></main>'
           if industry_src
           else f'<main id="industry-view" class="view">{industry_html}</main>'
           if industry_html else '')
        + (f'<main id="heatmap-view" class="view">{heatmap_html}</main>'
           if heatmap_html else '')
        + '<div id="modal" class="modal" hidden>'
        '<div class="modal-backdrop"></div>'
        '<div class="modal-content">'
        '<button class="modal-close" type="button" aria-label="닫기">×</button>'
        '<div id="modal-body"></div>'
        '</div>'
        '</div>'
        '<script>'
        f"const ALERTS={payload_json};\n"
        f"const HISTORY_SRC={history_src_json};\n"   # 모달 히스토리 lazy 파일(빈문자=인라인)
        f"const LATEST_IDS=new Set({latest_ids_json});\n"
        f"const STOCK_QUOTES={stock_quotes_json};\n"
        + _JS
        # 월별 원자료 가로 스크롤 = 최신(우측) 디폴트 (사용자 2026-06-15 '맨 오른쪽
        # 디폴트'). ⚠️ 산업트렌드는 탭이라 숨겨진 동안 scrollWidth=0 → 탭 핸들러가
        # 월별표 최신(우측) 디폴트 = CSS .ind-raw-scroll{direction:rtl} 가 레이아웃으로
        # 보장(타이밍 무관). JS scrollLeft 설정은 rtl 의 브라우저별 scrollLeft 규약과
        # 충돌해 우측 디폴트를 도로 좌측으로 밀던 케이스 발생(사용자 2026-06-20 일부 표
        # 2026.2 노출) → 제거. _scrollRaw 는 호출부(_lazyFetchView·탭·펼침) 안전용 no-op.
        + ("\n;(function(){window._scrollRaw=function(){};})();")
        + '</script>' + SCROLL_RESTORE_JS + '</body></html>'
    )


_CSS = """
:root{
  --bg:#f5f5f7;--surface:#fff;--surface-2:#fafafd;--text:#1d1d1f;
  --text-sub:#6e6e73;--border:#d2d2d7;--border-soft:#e5e5e7;
  --chip-bg:#f0f0f0;--accent:#0071e3;
  --tone-export:#34c759;--tone-import:#ff9500;
  --b-export-bg:#d1f4d8;--b-export-fg:#1f7a32;
  --b-import-bg:#fff0d1;--b-import-fg:#8a5a00;
  --b-prelim-bg:#eee;--b-prelim-fg:#6e6e73;
  --b-final-bg:#c8e6ff;--b-final-fg:#003e7e;
  --b-comp-bg:#ffe0e0;--b-comp-fg:#8a2020;
  --shadow:0 1px 3px rgba(0,0,0,.06);
  --img-placeholder:#e5e5e7;
}
body.dark{
  --bg:#1a1a1c;--surface:#2c2c2e;--surface-2:#252527;--text:#f5f5f7;
  --text-sub:#b8b8bd;--border:#3a3a3c;--border-soft:#3a3a3c;
  --chip-bg:#3a3a3c;--accent:#0a84ff;
  --tone-export:#30d158;--tone-import:#ff9f0a;
  --b-export-bg:#0f3a1a;--b-export-fg:#5fd778;
  --b-import-bg:#3a2807;--b-import-fg:#ffb84d;
  --b-prelim-bg:#3a3a3c;--b-prelim-fg:#98989d;
  --b-final-bg:#0a2a4d;--b-final-fg:#7ab6ff;
  --b-comp-bg:#4a1a1a;--b-comp-fg:#ff7b7b;
  --shadow:0 1px 3px rgba(0,0,0,.4);
  --img-placeholder:#1f1f21;
}
*{box-sizing:border-box}
/* 카드 클릭 시 모달 open 의 body{overflow:hidden}(스크롤 잠금)이 페이지 스크롤바를
   없애 폭이 ~15px 변하면, 1054+ 카드 전체가 reflow 돼 체감 지연(사용자 2026-06-15
   '카드 여는거 느림'). 스크롤바 공간을 항상 예약해 폭 변동·reflow 제거 — 긴 목록
   페이지의 표준 모달-지연 해법. */
html{scrollbar-gutter:stable}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif;margin:0;padding:0;background:var(--bg);color:var(--text);line-height:1.4;transition:background .25s,color .25s}
header{background:var(--surface);padding:14px 18px;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
h1{margin:0 0 4px;font-size:18px}
.meta{font-size:12px;color:var(--text-sub);line-height:1.5}
.meta-status,.meta-next,.meta-today,.meta-backlog,.meta-quote{margin-top:2px}
.meta-status:empty,.meta-next:empty,.meta-today:empty,.meta-backlog:empty{display:none}
.meta-status strong{color:var(--text);font-weight:600}
.meta-next strong{color:var(--accent);font-weight:600}
.meta-today strong{color:var(--text);font-weight:600}
.meta-backlog{color:var(--text-sub)}
.meta-backlog strong{color:var(--b-import-fg);font-weight:600}
.meta-backlog code{font-size:10.5px;background:var(--surface-2);padding:0 4px;border-radius:3px}
.meta-cost{margin-top:2px;color:var(--text-sub)}
.meta-cost:empty{display:none}
/* 📡 자동화 플로우 노트(상단 접이식, 사용자 2026-06-18) — 테마변수라 다크/라이트 자동 */
.flow-note{background:var(--surface-2);border-bottom:1px solid var(--border);padding:8px 18px;font-size:12px}
.flow-note summary{cursor:pointer;font-weight:600;color:var(--text-sub);list-style:none}
.flow-note summary::-webkit-details-marker{display:none}
.flow-note summary::before{content:"▸ ";color:var(--text-sub)}
.flow-note[open] summary::before{content:"▾ "}
.flow-note .flow-body{margin-top:6px;color:var(--text-sub);line-height:1.7}
.flow-note .flow-body b{color:var(--text)}
.customs-panel{background:var(--surface);border-bottom:1px solid var(--border);padding:8px 18px}
.customs-panel summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--text);list-style:none}
.customs-panel summary::-webkit-details-marker{display:none}
.customs-panel summary::before{content:"▸ ";color:var(--text-sub)}
.customs-panel[open] summary::before{content:"▾ "}
.customs-note{font-size:11px;color:var(--text-sub);margin:6px 0 8px}
.customs-table{width:100%;border-collapse:collapse;font-size:12.5px}
.customs-table th,.customs-table td{padding:5px 8px;border-bottom:1px solid var(--border-soft);text-align:left}
.customs-table th{font-size:11px;color:var(--text-sub);font-weight:600}
.customs-table td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.customs-table .hs{font-size:10px;color:var(--text-sub)}
.customs-table .num.up{color:var(--tone-export)}
.customs-table .num.down{color:var(--tone-import)}
.customs-table tr.nodata td{color:var(--text-sub);font-style:italic}
.archive-month{border-top:1px solid var(--border-soft)}
.archive-month summary{padding:6px 14px;cursor:pointer;font-weight:600;font-size:12px;color:var(--text-sub);background:var(--surface)}
.archive-month ul{margin:0;padding:4px 14px 8px 26px;font-size:13px;line-height:1.6;list-style:none}
.archive-month li{margin:1px 0}
.archive-month .muted{font-size:11px;color:var(--text-sub)}
/* 산업트렌드 탭 */
.ind-panel[hidden]{display:none !important}
.ind-summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;padding:4px 16px 0}
.ind-deriv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;padding:12px 16px 4px}
.ind-sbox{background:var(--surface);border:1px solid var(--border-soft);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.ind-sbox h3{margin:0 0 8px;font-size:14px}
.ind-sbox-hot h3{color:#1f7a32}.ind-sbox-turn h3{color:#8a5a00}.ind-sbox-down h3{color:#8a2020}
.ind-sbox-accel h3{color:#1f7a32}.ind-sbox-decel h3{color:#8a2020}
.ind-sbox-imp h3{color:var(--accent)}
.ind-imp-list{display:flex;flex-direction:column;gap:6px}
.ind-imp-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.ind-imp-drv{font-size:11px;color:var(--text-sub)}
.ind-imp-tag{font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;white-space:nowrap}
.ind-imp-tag-lead{background:rgba(245,158,11,.18);color:#b45309}
.ind-imp-tag-coin{background:var(--surface-2);color:var(--text-sub)}
body.dark .ind-imp-tag-lead{background:rgba(245,158,11,.22);color:#fcd34d}
.ind-imp-cap{margin-left:5px;font-size:9px;font-weight:700;padding:1px 5px;border-radius:8px;background:rgba(16,185,129,.16);color:#047857}
body.dark .ind-imp-cap{background:rgba(16,185,129,.2);color:#6ee7b7}
.ind-sbox-info p,.ind-sbox-sub{margin:0 0 6px;font-size:12px;color:var(--text-sub);line-height:1.5}
.ind-chip-wrap{display:flex;flex-wrap:wrap;gap:6px}
.ind-mini-chip{font-size:12px;background:var(--chip-bg);border-radius:999px;padding:3px 9px}
.ind-mini-chip .pos{color:var(--tone-export);font-weight:600}
.ind-mini-chip .neg{color:var(--tone-import);font-weight:600}
.ind-topbar{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;padding:10px 16px}
.ind-note{font-size:12px;color:var(--text-sub)}
/* 확정 데이터 빈티지 강조 badge (사용자 2026-06-15 '잠정 badge 처럼 강조') —
   잠정(초록 ind-prov-cur)과 구분되게 확정은 파란 accent pill. */
.ind-note-cur{font-size:11px;font-weight:700;color:var(--accent);border:1px solid var(--accent);border-radius:999px;padding:1px 8px;margin:0 3px;vertical-align:middle;white-space:nowrap}
.ind-motie{margin:0 16px 6px;padding:8px 12px;border:1px solid var(--border-soft);border-left:3px solid var(--accent);border-radius:8px;background:var(--surface);font-size:12.5px}
.ind-motie a{color:var(--accent);text-decoration:none;font-weight:600}
.ind-motie a:hover{text-decoration:underline}
.ind-motie-note{color:var(--text-sub)}
/* 🔍 LLM 추가신호 박스 */
.ins-box{margin:8px 16px 4px;padding:14px 16px;border:1px solid var(--border-soft);border-left:4px solid var(--accent);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
.ins-box>h3{margin:0 0 2px;font-size:16px;color:var(--accent)}
.ins-sub{font-size:12px;color:var(--text-sub);margin-bottom:10px}
.ins-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.ins-card{background:var(--bg);border:1px solid var(--border-soft);border-radius:10px;padding:10px 12px}
.ins-card h4{margin:0 0 4px;font-size:13px;color:var(--text)}
.ins-card p{margin:0;font-size:12px;color:var(--text-sub);line-height:1.55}
/* 🟢 잠정 속보(관세청 10일 단위) 박스 — 산업 집계와 분리된 선행 신호 */
.ind-prov{margin:8px 16px 4px;padding:14px 16px;border:1px solid var(--border-soft);border-left:4px solid var(--tone-export);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
.ind-prov>h3{margin:0 0 2px;font-size:16px;color:var(--tone-export)}
.ind-prov-tag{font-size:11px;font-weight:600;color:var(--text-sub);border:1px solid var(--border-soft);border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:middle}
.ind-prov-cur{font-size:11px;font-weight:700;color:var(--tone-export);border:1px solid var(--tone-export);border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:middle}
/* 영역 구분선 — 강조 pill (사용자 2026-06-12 '좀 더 선명하게') */
.ind-zone-div{display:flex;align-items:center;gap:12px;margin:28px 8px 14px;color:var(--text);font-size:14.5px;font-weight:700}
.ind-zone-div::before,.ind-zone-div::after{content:'';flex:1;height:2px;background:var(--accent);opacity:.5;border-radius:2px}
.ind-zone-div span{background:var(--surface);border:1.5px solid var(--accent);border-radius:999px;padding:7px 16px;white-space:normal}
.ind-zone-div b{color:var(--accent)}
.ind-zone-div.prov::before,.ind-zone-div.prov::after{background:#34c759}
.ind-zone-div.prov span{border-color:#34c759}
.ind-zone-div.prov b{color:#34c759}
tr.ind-mti-row{cursor:pointer}
tr.ind-mti-row:hover td{background:var(--surface-2)}
.ind-mti-more{color:var(--text-sub);font-size:11px;margin-left:4px}
tr.ind-mti-d>td{background:var(--surface);padding:10px 12px}
.ind-base-tag{display:inline-block;margin-left:4px;padding:1px 6px;border-radius:999px;font-size:10px;font-weight:700;background:#ff950022;color:#ff9500;cursor:help}
.ind-extra{font-size:12px;color:var(--text);margin:6px 0 0;line-height:1.5}
.ind-extra-sub{color:var(--text-sub);font-size:11px;cursor:help}
.ind-code{display:inline-block;margin-left:3px;padding:0 4px;border-radius:5px;font-size:10px;font-family:ui-monospace,Menlo,monospace;background:var(--surface-2);color:var(--text-sub);vertical-align:1px}
.ind-prov-sub{font-size:12px;color:var(--text-sub);margin-bottom:10px}
.ind-prov-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.ind-prov-cell{background:var(--bg);border:1px solid var(--border-soft);border-radius:10px;padding:10px 12px}
.ind-prov-k{font-size:12px;color:var(--text-sub);margin-bottom:3px}
.ind-prov-v{font-size:15px;font-weight:700;color:var(--text)}
.ind-prov-pos{color:var(--tone-export);font-size:13px;font-weight:600}
.ind-prov-neg{color:var(--tone-import);font-size:13px;font-weight:600}
.ind-prov-flat{color:var(--text-sub);font-size:13px}
.ind-prov-warn{border-color:var(--tone-import)}
.ind-prov-cav{margin-top:8px;font-size:11.5px;color:var(--tone-import);line-height:1.4}
.ind-prov-arch{margin-top:8px;font-size:12.5px}
.ind-prov-arch a{color:var(--tone-export);text-decoration:none;font-weight:600}
.ind-prov-arch a:hover{text-decoration:underline}
.ind-prov-more{margin-top:10px}
.ind-prov-more>summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--tone-export);list-style:none;padding:6px 0}
.ind-prov-more>summary::-webkit-details-marker{display:none}
.ind-prov-more>summary::before{content:'▸ ';color:var(--text-sub)}
.ind-prov-more[open]>summary::before{content:'▾ '}
.ind-prov-mom{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:6px}
.ind-prov-mom>.ind-prov-sort,.ind-prov-mom>.ind-prov-mom-note{grid-column:1/-1}
@media(max-width:980px){.ind-prov-mom{grid-template-columns:1fr}}
.ind-prov-mom-note{grid-column:1/-1;font-size:11.5px;color:var(--text-sub);line-height:1.4}
.ind-prov-tbl{width:100%;border-collapse:collapse;font-size:12px;background:var(--bg);border:1px solid var(--border-soft);border-radius:8px;overflow:hidden}
.ind-prov-tbl caption{caption-side:top;text-align:left;font-weight:600;font-size:12.5px;padding:6px 8px;color:var(--text)}
.ind-prov-tbl th{text-align:right;font-weight:500;color:var(--text-sub);padding:4px 8px;border-bottom:1px solid var(--border-soft)}
.ind-prov-tbl th:first-child{text-align:left}
.ind-prov-tbl td{padding:4px 8px;border-bottom:1px solid var(--border-soft);color:var(--text)}
.ind-prov-tbl td:first-child{font-weight:600}
.ind-prov-num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.ind-prov-trtot td{font-weight:700;background:var(--surface-2)}
.ind-prov-cap-warn{font-size:11px;font-weight:500;color:var(--tone-import);margin-left:6px}
.ind-prov-sort{grid-column:1/-1;display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--text-sub);margin-bottom:2px}
.ind-prov-sort-btn{padding:3px 10px;border-radius:999px;border:1px solid var(--border-soft);background:var(--bg);color:var(--text-sub);font-size:11.5px;font-weight:600;cursor:pointer}
.ind-prov-sort-btn.is-active{background:var(--tone-export);color:#fff;border-color:var(--tone-export)}
/* 🗄 월별 아카이브 링크 섹션 */
.ind-archive{margin:8px 16px 0;padding:9px 14px;border:1px dashed var(--border-soft);border-radius:10px;background:var(--surface)}
.ind-archive a{color:var(--accent);text-decoration:none;font-size:13px;font-weight:600}
.ind-archive a:hover{text-decoration:underline}
.ind-share{display:inline-flex;align-items:center;flex-wrap:wrap;margin-left:16px}
.ind-share-link{display:inline-block;padding:7px 14px;border:1px solid var(--accent);border-radius:8px;font-size:13px;font-weight:600;color:var(--accent);text-decoration:none;word-break:keep-all}
.ind-share-link:active{background:var(--accent);color:#fff}
.ind-legend{display:flex;gap:12px;font-size:12px;color:var(--text-sub)}
.ind-legend i{display:inline-block;width:14px;height:0;vertical-align:middle;margin-right:4px}
.ind-legend .ind-lg-v{border-top:3px solid var(--accent)}
.ind-legend .ind-lg-m{border-top:2px dashed #34c759}
.ind-axis{fill:var(--text-sub);font-size:9px}
.ind-cl{fill:var(--text);font-size:9px;font-weight:650;paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round}
.ind-cl-ma{fill:#2c9c47;font-size:9px;font-weight:650;paint-order:stroke;stroke:var(--surface);stroke-width:3px;stroke-linejoin:round}
.ind-group{font-size:15px;font-weight:700;margin:14px 16px 6px;padding-left:8px;border-left:3px solid var(--border)}
.ind-group-hot{border-left-color:#34c759}
.ind-group-turn{border-left-color:#ff9500}
.ind-group-down{border-left-color:#ff3b30}
.ind-group-na{border-left-color:var(--border)}
.ind-cards{display:flex;flex-direction:column;gap:12px;padding:0 16px 8px;min-width:0}
.ind-card{background:var(--surface);border:1px solid var(--border-soft);border-radius:10px;padding:14px;box-shadow:var(--shadow);min-width:0}
/* 두 차트 열(라인 cell1 · 막대 cell2)을 동일 너비로(같은 너비→같은 높이→정렬). meta
   좌·차트 우. 개별(.ind-mti-d)도 펼치면 전폭(ind-open)이라 이 그리드 그대로 = 기존과
   동일(사용자 2026-06-20 '기존처럼·심플'; 1fr+고정400 과교정 되돌림). */
.ind-row{display:grid;grid-template-columns:minmax(230px,0.95fr) minmax(260px,1.1fr) minmax(260px,1.1fr);gap:14px;align-items:start}
.ind-meta{min-width:0}
.ind-chart-cell{min-width:0}
.ind-chart-cell .ind-chart{width:100%;height:auto;max-width:400px}
@media(max-width:900px){.ind-row{grid-template-columns:1fr}}
/* 하위품목 랭킹표(.ind-sub-wrap, 2단 half-width) 안에서 행을 펼치면 _card_body 의
   .ind-row(3단 ~750px)가 반폭 셀을 넘쳐 표 전체가 가로로 밀리고 품목명(첫 칸)이
   왼쪽으로 잘려 나갔다(사용자 2026-06-18 '늄괴→뉴괴' 클리핑). 좁은 셀에선 차트를
   세로로 스택해 가로 넘침 자체를 없앤다(TOP10 풀카드 .ind-card 는 해당 없음). */
.ind-sub-wrap .ind-row{grid-template-columns:1fr}
/* 펼침 상세(.ind-mti-d) = TOP10 풀카드(.ind-card)와 동일 레이아웃: 코멘트(meta) +
   라인·막대 차트 2개 나란히 3단, 차트 max-width 400px 캡(사용자 2026-06-20 '개별항목도
   기본처럼 보이게·이상하게 크게 나오는거 수정'). .ind-sub-wrap .ind-row{1fr}(랭킹행
   스택)을 상세행만 기본 3단으로 복원, 차트는 기본 .ind-chart-cell .ind-chart{max-width:
   400px} 상속(옛 max-width:100% 오버사이즈 제거). 상세 td nowrap 해제(코멘트 줄바꿈).
   좁으면(<900px) 기본과 동일하게 1단. 월별 원자료 우측(최신) 스크롤은 _scrollRaw(JS). */
.ind-sub-wrap .ind-mti-d>td{white-space:normal;text-align:left}
/* _card_body 를 td 직속 grid 로 두면 표셀 폭 산정과 충돌해 차트 트랙이 0/거대로 깨짐
   (라인차트 빈칸·세로 빈공간, 사용자 2026-06-20 '계속 문제'). 블록 div(min-width:0)로
   감싸 기본 카드와 동일한 블록 포매팅 컨텍스트 부여 → grid 정상 산정. */
.ind-mti-card{min-width:0}
.ind-sub-wrap .ind-mti-d .ind-row{grid-template-columns:minmax(230px,0.95fr) minmax(260px,1.1fr) minmax(260px,1.1fr)}
@media(max-width:900px){.ind-sub-wrap .ind-mti-d .ind-row{grid-template-columns:1fr}}
/* 그래도 표가 넘칠 때 품목명 칸(랭킹 본문 첫 td)을 헤더처럼 sticky 고정(헤더
   th:first-child 와 짝) — 가로 스크롤해도 품목명이 안 잘리게. 펼침 상세행
   (.ind-mti-d, colspan 전폭)은 제외. */
.ind-table tr.ind-mti-row td:first-child{position:sticky;left:0;z-index:1;background:var(--surface);box-shadow:1px 0 0 var(--border-soft)}
.ind-head{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.ind-bar-pos{fill:#34c759}
.ind-bar-neg{fill:#ff3b30}
.ind-zero-line{stroke:var(--text-sub);stroke-width:1}
.ind-raw{margin-top:14px;border-top:1px solid var(--border-soft);padding-top:10px}
.ind-raw-title{font-size:12px;color:var(--text-sub);margin-bottom:6px;font-weight:600}
/* direction:rtl = 스크롤 컨테이너의 기본 위치를 '오른쪽(최신)'으로 — 레이아웃 기반이라
   reflow·lazy·hidden→show·전폭전환 타이밍과 무관하게 항상 최신부터 보임(JS scrollLeft
   타이밍 의존 종식, 사용자 2026-06-20 반복 '스크롤 풀림'). 표 내용은 direction:ltr 로
   정상(좌→우 시간축). 기본·개별 카드 공통(.ind-raw-scroll 단일 정의). */
.ind-raw-scroll{overflow-x:auto;direction:rtl}
.ind-table{border-collapse:separate;border-spacing:0;font-size:11px;line-height:1.35;white-space:nowrap;width:max-content;min-width:100%;direction:ltr}
.ind-table th,.ind-table td{border-top:1px solid var(--border-soft);border-right:1px solid var(--border-soft);padding:5px 8px;text-align:right;background:var(--surface)}
.ind-table thead th{background:var(--surface-2);color:var(--text-sub);font-weight:650}
.ind-table th:first-child{position:sticky;left:0;z-index:1;min-width:72px;text-align:left;background:var(--surface-2);box-shadow:1px 0 0 var(--border-soft)}
.ind-table td.pos{color:var(--tone-export)}
.ind-table td.neg{color:var(--tone-import)}
.ind-ttm-yoy-line{fill:none;stroke:#6d5bd0;stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.ind-ttm-yoy-dot{fill:#6d5bd0;stroke:#fff;stroke-width:1.5}
.ind-sub-note{font-size:12px;color:var(--text-sub);padding:0 16px 8px}
.ind-sub-wrap{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:0 16px 8px;min-width:0}
@media(max-width:860px){.ind-sub-wrap{grid-template-columns:1fr}}
/* 행 펼침 시 그 카드를 전폭으로(반대편 빈 공간까지) → 상세가 기본 카드와 같은 너비로
   (사용자 2026-06-20). 다시 접으면 JS 가 ind-open 제거 → 원래 2단 복귀. */
.ind-sub-wrap .ind-sub-card.ind-open{grid-column:1/-1}
/* min-width:0 = 그리드 아이템이 1fr 트랙으로 수축 → 안의 .ind-raw-scroll(월별표)이
   실제로 가로 스크롤(없으면 넓은 월별표가 카드를 밀어 페이지 블로우아웃·스크롤 0,
   사용자 2026-06-18 '스크롤도 없어'). .ind-card(#497)와 동일 패턴, sub-card 누락분. */
.ind-sub-card{background:var(--surface);border:1px solid var(--border-soft);border-radius:10px;padding:10px 12px;box-shadow:var(--shadow);min-width:0}
.ind-sub-card summary{cursor:pointer;font-size:13px;font-weight:600;margin-bottom:6px}
.ind-sub-ind{color:var(--text-sub)}
.ind-table td.pos{color:var(--tone-export)}.ind-table td.neg{color:var(--tone-import)}
.ind-head h3{margin:0;font-size:15px}
.ind-badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;color:#fff}
.ind-badge-hot{background:#34c759}
.ind-badge-turn{background:#ff9500}
.ind-badge-down{background:#ff3b30}
.ind-badge-na{background:#9b9b9f}
.ind-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:4px 8px;margin:0 0 8px}
.ind-stats div{display:flex;flex-direction:column}
.ind-stats dt{font-size:10px;color:var(--text-sub)}
.ind-stats dd{margin:0;font-size:13px;font-weight:600}
.ind-stats dd.pos{color:var(--tone-export)}
.ind-stats dd.neg{color:var(--tone-import)}
.ind-mstatus{font-size:10px;font-weight:500;color:var(--text-sub);white-space:nowrap}
.ind-chart{width:100%;height:auto;display:block;max-width:400px}
/* 차트 max-width 캡 (사용자 2026-06-17 '클릭 상세 너무 큼'): viewBox 380×132 가
   넓은 화면에서 셀 폭만큼 커져 차트·라벨이 거대 → 레퍼런스 원본(~380px) 수준으로
   제한. width:100% 라 좁은 화면은 그대로 반응형. */
.ind-grid{stroke:var(--border-soft);stroke-width:1}
.ind-value-line{fill:none;stroke:var(--accent);stroke-width:2.4;stroke-linejoin:round;stroke-linecap:round}
.ind-ma-line{fill:none;stroke:#34c759;stroke-width:2;stroke-dasharray:5 4;stroke-linejoin:round;stroke-linecap:round}
.ind-latest-dot{fill:var(--accent);stroke:#fff;stroke-width:1.5}
.ind-summary{font-size:12px;color:var(--text);margin:2px 0 4px}
.ind-signal{font-size:12px;color:var(--text-sub);margin:0 0 8px;padding:5px 8px;background:var(--surface-2);border-radius:6px}
.ind-signal b{color:var(--text)}
.ind-toggle{display:inline-flex;gap:0;margin-bottom:6px;border:1px solid var(--border);border-radius:7px;overflow:hidden}
.ind-tg-btn{padding:4px 12px;background:var(--surface);border:none;font-size:11px;font-weight:600;color:var(--text-sub);cursor:pointer}
.ind-tg-btn.is-active{background:var(--accent);color:#fff}
.ind-chart-title{font-size:11px;color:var(--text-sub);margin-bottom:2px}
.ind-na{font-size:12px;color:var(--text-sub);padding:24px 8px;text-align:center}
.tabs{display:flex;background:var(--surface);border-bottom:1px solid var(--border);position:sticky;top:60px;z-index:9}
.tab{flex:1;padding:13px 0;background:none;border:none;font-size:14px;font-weight:600;color:var(--text-sub);cursor:pointer;border-bottom:2px solid transparent}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.filters{background:var(--surface);padding:10px 18px;border-bottom:1px solid var(--border);position:sticky;top:108px;z-index:8}
/* 🏢 기업 보고서 위젯 (사용자 2026-06-17) */
.report-box{background:var(--surface);border-bottom:1px solid var(--border);padding:10px 18px}
.rb-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.rb-title{font-weight:700;font-size:14px;white-space:nowrap}
#rb-q{flex:1;min-width:180px;padding:8px 10px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;font-size:14px}
.rb-btn{padding:8px 12px;border:1px solid var(--border);background:var(--surface-2);color:var(--text);border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
.rb-btn:hover{border-color:var(--accent)}
.rb-btn.rb-llm{background:var(--accent);color:#fff;border-color:var(--accent)}
.rb-result{margin-top:10px;background:var(--card);border:1px solid var(--border-soft);border-radius:10px;padding:14px 16px}
.rb-result:empty{display:none}
.rb-note{color:var(--muted);font-size:13px;padding:6px 0}
.report-archive-link{background:var(--surface);border-bottom:1px solid var(--border);padding:8px 18px}
.report-archive-link a{color:var(--accent);text-decoration:none;font-size:13px;font-weight:600}
.report-archive-link a:hover{text-decoration:underline}
#q{width:100%;padding:9px 12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:8px;font-size:14px;margin-bottom:8px}
.chips{display:flex;gap:14px;flex-wrap:wrap}
.chip-group{display:flex;gap:3px;flex-wrap:wrap}
.chip{padding:5px 11px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:14px;font-size:12px;cursor:pointer}
.chip.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.count{margin-top:7px;font-size:11px;color:var(--text-sub)}
.view{display:none;padding:12px}
.view.active{display:block}
.section{background:var(--surface);border-radius:12px;margin-bottom:14px;overflow:hidden;box-shadow:var(--shadow)}
.section-header{padding:13px 14px;background:var(--surface-2);border-bottom:1px solid var(--border-soft)}
.section-header h2{margin:0;font-size:15.5px}
.section-header .sub-line{margin-top:4px;font-size:12.5px;color:var(--text-sub);word-break:keep-all;line-height:1.4}
.section-items{padding:8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.mini-card{border:1px solid var(--border-soft);border-radius:8px;overflow:hidden;cursor:pointer;transition:transform .1s;background:var(--surface);position:relative}
.mini-card:hover{transform:translateY(-1px)}
.mini-card .mini-img{height:90px;background:var(--img-placeholder);background-size:cover;background-position:center}
.mini-card .mini-text{padding:8px 10px;font-size:13px;line-height:1.35}
.mini-card .mini-text strong{display:block;margin-bottom:2px;font-size:13.5px;font-weight:600;word-break:keep-all;color:var(--text)}
.mini-card .mini-text span{color:var(--text-sub);font-size:12.5px}
.mini-card .dot{position:absolute;top:6px;right:6px;width:8px;height:8px;border-radius:4px;background:#999}
.mini-card .mini-new{position:absolute;top:5px;left:5px;padding:1px 6px;font-size:9px;font-weight:700;letter-spacing:.5px;background:#ff3b30;color:#fff;border-radius:3px;z-index:1;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.section-header .section-new{display:inline-block;margin-left:6px;padding:1px 6px;font-size:10px;font-weight:700;letter-spacing:.5px;background:#ff3b30;color:#fff;border-radius:3px;vertical-align:middle}
.mini-card.export .dot{background:var(--tone-export)}
.mini-card.import .dot{background:var(--tone-import)}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10px;font-weight:600;margin-right:3px;letter-spacing:.3px}
.badge.export{background:var(--b-export-bg);color:var(--b-export-fg)}
.badge.import{background:var(--b-import-bg);color:var(--b-import-fg)}
.badge.preliminary{background:var(--b-prelim-bg);color:var(--b-prelim-fg)}
.badge.final{background:var(--b-final-bg);color:var(--b-final-fg)}
.badge.composite{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.badge.sla-pending{background:var(--b-prelim-bg);color:var(--text)}
.badge.sla-due{background:var(--b-import-bg);color:var(--b-import-fg)}
.badge.sla-late{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.mini-sla{display:inline-block;margin-top:4px;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;letter-spacing:.2px}
.mini-sla.pending{background:var(--b-prelim-bg);color:var(--text-sub)}
.mini-sla.due{background:var(--b-import-bg);color:var(--b-import-fg)}
.mini-sla.late{background:var(--b-comp-bg);color:var(--b-comp-fg)}
.csv-btn{display:inline-flex;align-items:center;gap:4px;background:var(--surface);color:var(--text);border:1px solid var(--border);padding:5px 11px;border-radius:14px;font-size:12px;cursor:pointer;margin-left:auto}
.csv-btn:hover{background:var(--surface-2)}
.filters .top-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.filters .top-row #q{flex:1;margin-bottom:0}
.empty{padding:40px 20px;text-align:center;color:var(--text-sub);font-size:13px}
.country-mix{padding:8px 14px 4px}
.mix-bar{width:100%;height:18px;display:block;border-radius:3px;overflow:hidden;background:var(--border-soft)}
.mix-legend{margin-top:6px;display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:var(--text-sub)}
.mix-legend-item{display:inline-flex;align-items:center;gap:3px}
.mix-legend-item em{font-style:normal;color:var(--text);font-weight:600}
.mix-dot{display:inline-block;width:8px;height:8px;border-radius:2px}
.mix-legend-more{color:var(--text-sub);font-style:italic}
.matrix-meta{padding:14px 18px 8px;font-size:12px;color:var(--text-sub);line-height:1.5}
.matrix-scroll{overflow:auto;max-width:100%;padding:0 12px 12px;-webkit-overflow-scrolling:touch}
.matrix-table{border-collapse:separate;border-spacing:0;font-size:11px;color:var(--text);background:var(--surface);border-radius:8px;box-shadow:var(--shadow)}
.matrix-table th,.matrix-table td{padding:4px 6px;border-right:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft);text-align:center;white-space:nowrap}
.matrix-table th.row-head{text-align:left;background:var(--surface-2);position:sticky;left:0;font-weight:600;max-width:200px;overflow:hidden;text-overflow:ellipsis;z-index:1}
.matrix-table th.row-head em{font-style:normal;color:var(--text-sub);margin-left:4px;font-size:10px}
.matrix-table th.col-head{background:var(--surface-2);position:sticky;top:0;font-weight:600;min-width:60px;z-index:1}
.matrix-table th.col-head span{display:block}
.matrix-table th.col-head em{font-style:normal;color:var(--text-sub);font-size:10px}
.matrix-table thead th.row-head{z-index:2}
.matrix-table td.cell{cursor:pointer;font-weight:600;color:#fff;text-shadow:0 0 2px rgba(0,0,0,.5);min-width:32px}
.matrix-table td.cell.empty-cell{background:transparent;cursor:default;color:transparent}
.matrix-table td.cell:hover:not(.empty-cell){outline:2px solid var(--accent);outline-offset:-2px;z-index:1}
.modal{position:fixed;inset:0;z-index:100;display:flex;align-items:flex-start;justify-content:center}
.modal[hidden]{display:none}
.modal-backdrop{position:absolute;inset:0;background:rgba(0,0,0,.7);cursor:pointer}
.modal-content{position:relative;background:var(--surface);color:var(--text);width:100%;max-width:880px;margin:20px;border-radius:14px;overflow:hidden;display:flex;flex-direction:column;max-height:calc(100vh - 40px);box-shadow:0 10px 40px rgba(0,0,0,.3)}
.modal-close{position:absolute;top:10px;right:10px;width:34px;height:34px;background:rgba(0,0,0,.55);color:#fff;border:none;border-radius:17px;font-size:20px;line-height:1;cursor:pointer;z-index:2}
#modal-body{overflow-y:auto;flex:1}
.modal-head{padding:18px 22px;border-bottom:1px solid var(--border-soft)}
.modal-head h2{margin:0 0 8px;font-size:18px;word-break:keep-all;padding-right:40px}
.modal-head .period-label{margin-top:8px;font-size:13px;color:var(--text);font-weight:500}
.modal-head .sub{margin-top:4px;font-size:12.5px;color:var(--text-sub)}
.stocks{margin-top:10px;font-size:12px}
.stocks .label{color:var(--text-sub);margin-right:5px}
.stock{display:inline-block;padding:3px 8px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border-radius:4px;font-size:11px}
.stock-px{font-weight:600;margin-left:4px}
.stock-px.up{color:var(--tone-export)}
.stock-px.down{color:var(--tone-import)}
.section-px{font-size:14px;font-weight:700;margin-left:8px;vertical-align:middle}
.section-px.up{color:var(--tone-export)}
.section-px.down{color:var(--tone-import)}
.px-asof{font-size:.8em;color:#888;font-weight:400;margin-left:2px}
.modal-images{display:flex;flex-direction:column;gap:1px;background:var(--bg)}
.modal-images img{width:100%;display:block;background:var(--img-placeholder)}
.modal-text{padding:14px 22px;font-size:13px;color:var(--text-sub);white-space:pre-wrap;border-top:1px solid var(--border-soft);background:var(--surface-2)}
.modal-toolbar{margin-top:10px;display:flex;gap:6px;flex-wrap:wrap}
.tool-btn{padding:5px 11px;font-size:12px;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:14px;cursor:pointer}
.tool-btn:hover:not(:disabled){background:var(--surface-2)}
.tool-btn:disabled{opacity:.6;cursor:default}
.modal-links,.modal-peers{margin-top:10px;font-size:12px}
.modal-links .label,.modal-peers .label{color:var(--text-sub);margin-right:6px;font-size:11px}
.link-chip{padding:3px 9px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border:1px solid var(--border-soft);border-radius:4px;font-size:11px;cursor:pointer;font-weight:500}
.link-chip:hover{background:var(--accent);color:#fff;border-color:var(--accent)}
.peer-chip{display:inline-block;padding:2px 7px;margin:2px 3px 0 0;background:var(--chip-bg);color:var(--text);border-radius:4px;font-size:11px;cursor:pointer}
.peer-chip:hover{background:var(--accent);color:#fff}
.modal-card{display:block}
.modal-card.secondary{border-top:1px solid var(--border-soft);cursor:pointer;transition:background .15s}
.modal-card.secondary:hover{background:var(--surface-2)}
.modal-card.secondary .modal-head{padding:14px 22px}
.modal-card.secondary .modal-head .sib-title{margin:0;font-size:13px;font-weight:600;color:var(--text)}
.modal-divider{padding:14px 22px 4px;font-size:11px;color:var(--text-sub);font-weight:600;text-align:center;background:var(--surface-2);border-top:1px solid var(--border-soft)}
@media (max-width:600px){
  header{padding:12px 14px}.filters{padding:10px 14px}.view{padding:8px}
  .section-items{grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
  .modal-content{margin:0;border-radius:0;max-height:100vh}
  /* Mobile readability — bump body text by ~1px over the desktop
     base. Badges / chips / matrix cells are intentionally NOT
     resized: they're short tokens that already read fine at 10-12px
     and enlarging them breaks the card grid / matrix cell layout. */
  .meta{font-size:12.5px}
  .section-header h2{font-size:16px}
  .section-header .sub-line{font-size:13px}
  .mini-card .mini-text{font-size:13.5px}
  .mini-card .mini-text strong{font-size:14px}
  .mini-card .mini-text span{font-size:13px}
  .modal-head .sub{font-size:13px}
  .modal-text{font-size:13.5px}
}
"""


_JS = r"""
// --- helpers ---
function esc(s){return s==null?'':String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
// STOCK_QUOTES 조회 — 정확일치 → 파서 누수 접두사 제거 → 1차 토큰(슬래시·
// 중점·콤마) → 다중 공백 토큰의 첫 단어. 가능한 폴백을 순서대로 시도.
function pxLookup(name){
  if(typeof STOCK_QUOTES==='undefined'||!name)return null;
  var s=String(name);
  if(STOCK_QUOTES[s])return STOCK_QUOTES[s];
  s=s.replace(/^관련종목\s*:\s*/,'');
  if(STOCK_QUOTES[s])return STOCK_QUOTES[s];
  var first=s.split(/[\/·,]/)[0].trim();
  if(first.endsWith(' 등'))first=first.slice(0,-2).trim();
  if(first&&STOCK_QUOTES[first])return STOCK_QUOTES[first];
  // 다중 공백 토큰의 첫 단어(서버 split_names와 동일 규칙)
  if(first.indexOf(' ')>0){
    var w=first.split(/\s+/)[0];
    if(STOCK_QUOTES[w])return STOCK_QUOTES[w];
  }
  return null;
}
// 관련종목 시세 칩 — 매칭되는 종목만 +등락률. 없으면 빈 문자열(이름만).
function stockPx(name){
  var q=pxLookup(name); if(!q)return '';
  var c=Number(q.c)||0, cls=c>=0?'up':'down', sign=c>=0?'+':'';
  var asof=q.d?' <span class="px-asof">('+q.d+')</span>':'';
  var tt=(q.d?'('+q.d+') 종가 ':'종가 ')+(Number(q.p)||0).toLocaleString()+'원';
  return ' <span class="stock-px '+cls+'" title="'+tt+'">'+sign+c.toFixed(1)+'%</span>'+asof;
}
// 회사별 섹션 헤더용 — 회사명=종목이므로 종가+등락률을 더 크게.
function sectionStockPx(name){
  var q=pxLookup(name); if(!q)return '';
  var c=Number(q.c)||0, cls=c>=0?'up':'down', sign=c>=0?'+':'';
  var asof=q.d?' <span class="px-asof">('+q.d+' 종가)</span>':'';
  return ' <span class="section-px '+cls+'" title="종가">'+
    (Number(q.p)||0).toLocaleString()+'원 '+sign+c.toFixed(1)+'%</span>'+asof;
}
function whereLabel(a){return [a.region,a.country].filter(Boolean).join(' → ')}

// Build a one-shot dedup_key → alerts[] index so the modal can find
// sibling history alerts (and so the views don't have to repeat work).
const BY_DEDUP={};
ALERTS.forEach(a=>{(BY_DEDUP[a.dedup_key]=BY_DEDUP[a.dedup_key]||[]).push(a)});

// 모달 조회용 사전 인덱스 (클릭마다 ALERTS 풀스캔 → O(1) 조회, 사용자 2026-06-15
// '개별 카드 클릭 너무 느림'). ALERT_BY_ID=클릭 룩업, BY_ITEM=품목별 alerts,
// COMPOSITE_BY_PART=합산 부품명→합산 alerts (findCompositeLinks/findPeerStocks 의
// ALERTS.forEach 풀스캔 제거 — 카드 클릭 O(n)→O(소수)).
const ALERT_BY_ID=new Map();
const BY_ITEM={};
const COMPOSITE_BY_PART={};
ALERTS.forEach(a=>{
  ALERT_BY_ID.set(a.id,a);
  (BY_ITEM[a.item]=BY_ITEM[a.item]||[]).push(a);
  if(a.is_composite){
    (a.composite_parts||[]).forEach(p=>{
      const k=(p||'').trim();
      if(k)(COMPOSITE_BY_PART[k]=COMPOSITE_BY_PART[k]||[]).push(a);
    });
  }
});

// 모달 히스토리 lazy (2026-06-16 '최신만 인라인 + 모달 히스토리 on-demand') —
// 과거 발표(siblings)는 alerts_history.json 으로 빼 초기 인라인 ALERTS=최신만.
// 모달 첫 클릭 때만 fetch → BY_DEDUP/ALERT_BY_ID 병합 → 열린 모달 재렌더. 뷰/검색/
// CSV 는 isLatest 라 무영향. fetch 실패 시 최신만 표시(graceful, 크래시 0).
let _histLoaded=false, _openAlert=null;
function _loadHistory(){
  if(_histLoaded||!HISTORY_SRC) return;
  _histLoaded=true;
  fetch(HISTORY_SRC,{cache:'no-store'})
    .then(function(r){if(!r.ok)throw 0;return r.json();})
    .then(function(hist){
      hist.forEach(function(a){
        (BY_DEDUP[a.dedup_key]=BY_DEDUP[a.dedup_key]||[]).push(a);
        ALERT_BY_ID.set(a.id,a);
      });
      // 모달이 (siblings 로드 전) 열려있으면 이전발표 채워 재렌더.
      if(_openAlert){
        const mb=document.getElementById('modal-body');
        if(mb)mb.innerHTML=renderModalBody(_openAlert);
      }
    })
    .catch(function(){_histLoaded=false;});   // 실패 → 다음 모달서 재시도(최신만 표시)
}

function isLatest(a){return LATEST_IDS.has(a.id)}

// Union of stocks across a section's variants, sorted by frequency
// (so the most-mentioned company shows first). Drives the section
// subtitle in 품목별 view so the operator sees the relevant company
// names without opening every mini-card.
function unionStocks(variants){
  const counts={};
  variants.forEach(a=>{(a.stocks||[]).forEach(s=>{counts[s]=(counts[s]||0)+1})});
  return Object.entries(counts)
    .sort((x,y)=>y[1]-x[1]||x[0].localeCompare(y[0]))
    .map(([n])=>n);
}

function stocksSubtitle(variants){
  const s=unionStocks(variants);
  if(!s.length)return '';
  // '·' separator reads more softly than '/' for Korean company names
  // sitting next to each other in a single line.
  if(s.length<=3)return '관련종목: '+s.join(' · ');
  return '관련종목: '+s.slice(0,3).join(' · ')+' 외 '+(s.length-3)+'개';
}

// Expected 확정 (final) date for a 잠정 (preliminary) alert.
// Per CLAUDE.md / BeOn schedule: 관세청 publishes the previous month's
// 확정 around the 15th of the following month. So for any 잠정 whose
// period_start is in month M, the expected 확정 announcement is
// (M+1) 월 15일 KST.
function expectedFinalKst(a){
  if(a.status==='final')return null;
  const ps=a.period_start||'';
  if(!ps)return null;
  const [y,m]=ps.split('-').map(Number);
  if(!y||!m)return null;
  const ny=m===12?y+1:y;
  const nm=m===12?1:m+1;
  return ny+'-'+String(nm).padStart(2,'0')+'-15';
}

function kstTodayString(){
  const utcMs=Date.now();
  // KST = UTC + 9h. Shift the timestamp so a UTC midnight in KST
  // round-trips to the right calendar date when sliced.
  return new Date(utcMs+9*3600000).toISOString().slice(0,10);
}

// Next-up BeOn publication date. The schedule (KST):
//   매월 11일경 — 1-10일 잠정
//   매월 21일경 — 1-20일 잠정
//   익월 1일경 — 전월 전체 잠정
//   익월 15일경 — 전월 전체 확정 (관세청)
// Returns {date, kind, daysUntil} for the first one strictly after today.
function nextAnnouncement(){
  const today=kstTodayString();
  const [y,m]=today.split('-').map(Number);
  const ny=m===12?y+1:y;
  const nm=m===12?1:m+1;
  const pm=m===1?12:m-1;  // 전월 (이달 15일 = 전월 전체 확정)
  const pad=n=>String(n).padStart(2,'0');
  // 이달 15일 '전월 전체 확정' 후보 누락 fix (사용자 2026-06-12 — 6/12에
  // 다음 발표가 6/21로 표시되며 6/15 5월 확정을 건너뛰던 버그).
  const cands=[
    {date:y+'-'+pad(m)+'-11', kind:m+'월 1-10일 잠정'},
    {date:y+'-'+pad(m)+'-15', kind:pm+'월 전체 확정'},
    {date:y+'-'+pad(m)+'-21', kind:m+'월 1-20일 잠정'},
    {date:ny+'-'+pad(nm)+'-01', kind:m+'월 전체 잠정'},
    {date:ny+'-'+pad(nm)+'-15', kind:m+'월 전체 확정'},
  ];
  const future=cands.filter(c=>c.date>today).sort((a,b)=>a.date.localeCompare(b.date));
  if(!future.length)return null;
  const f=future[0];
  return {date:f.date, kind:f.kind, daysUntil:daysBetween(today,f.date)};
}

// Period label for the current-data-status header line. Returns just
// the coverage period (no status word), e.g. "5월 1-20일", "4월 전체".
function latestPeriodStr(a){
  const ps=a.period_start||'';
  const m=parseInt(ps.slice(5,7));
  if(!m)return '';
  if(a.period_kind==='monthly')return m+'월 전체';
  if(a.period_kind==='decadal_10')return m+'월 1-10일';
  if(a.period_kind==='decadal_20')return m+'월 1-20일';
  const dEnd=(a.period_end||'').slice(8,10).replace(/^0/,'');
  return m+'월 '+dEnd+'일까지';
}

// Find the latest (by period_end) preliminary and final alert among the
// currently-latest revisions so the header can show "현재 잠정: X · 확정: Y".
function currentDataStatus(){
  let latestPrelim=null,latestFinal=null;
  ALERTS.forEach(a=>{
    if(!isLatest(a))return;
    const pe=a.period_end||a.period_start||'';
    if(!pe)return;
    if(a.status==='preliminary'){
      const cur=latestPrelim?latestPrelim.period_end||latestPrelim.period_start||'':'';
      if(pe>cur)latestPrelim=a;
    }else if(a.status==='final'){
      const cur=latestFinal?latestFinal.period_end||latestFinal.period_start||'':'';
      if(pe>cur)latestFinal=a;
    }
  });
  return {prelim:latestPrelim,final:latestFinal};
}

// 'Today's activity' summary. Counts the latest-visible alerts whose
// posted_at lands on today's KST date and separately how many of those
// are 확정 arrivals and how many bring a never-before-seen item into
// the corpus.
function quickStats(){
  const today=kstTodayString();
  let newToday=0,finalsToday=0,firstItemsToday=0;
  ALERTS.forEach(a=>{
    if(!isLatest(a))return;
    if(a.posted_at!==today)return;
    newToday++;
    if(a.status==='final')finalsToday++;
    if(EARLIEST_ITEM_DATE[a.item]===today)firstItemsToday++;
  });
  return {newToday,finalsToday,firstItemsToday};
}

// One-shot precompute used by the NEW badges and quickStats. For each
// item/company we cache the date of its first-ever alert; if that date
// is within 24 hours of today (KST), the latest card / company section
// shows a 'NEW' chip. cheap O(n) on page load.
const EARLIEST_ITEM_DATE={};
const EARLIEST_COMPANY_DATE={};
(function(){
  ALERTS.forEach(a=>{
    const pa=a.posted_at||'';
    if(!pa)return;
    if(!EARLIEST_ITEM_DATE[a.item]||pa<EARLIEST_ITEM_DATE[a.item]){
      EARLIEST_ITEM_DATE[a.item]=pa;
    }
    // 회사 신규 키는 회사별 섹션과 동일하게 companies(품목/제품 거른·복합명 분리)
    // 기준 — 원본 stocks면 'GST / 유니셈'이 split된 섹션에서 NEW를 놓침.
    (a.companies||a.stocks||[]).forEach(s=>{
      if(!EARLIEST_COMPANY_DATE[s]||pa<EARLIEST_COMPANY_DATE[s]){
        EARLIEST_COMPANY_DATE[s]=pa;
      }
    });
  });
})();

function withinDays(dateStr,n){
  if(!dateStr)return false;
  const today=kstTodayString();
  return daysBetween(dateStr,today)<=n;
}

// NEW badges:
//   alert-NEW   = the latest alert for this dedup_key was posted within
//                 the last 7 days (KST). Was 'today only', but 관세청
//                 잠정/확정 cycles are ≥10 days apart, so a 1-day window
//                 made a fresh card lose its NEW badge the next morning
//                 even though nothing newer had arrived. 7 days keeps a
//                 card flagged NEW for the whole inter-cycle gap. The
//                 '🆕 신규' filter chip shares this, so both move together.
//   item-NEW    = this item's earliest alert ever is within 7 days
//                 (the item itself debuted recently in our corpus)
//   company-NEW = same rule for a company name
function isAlertNew(a){return withinDays(a.posted_at||'',7)}
function isItemNew(item){return withinDays(EARLIEST_ITEM_DATE[item]||'',7)}
function isCompanyNew(name){return withinDays(EARLIEST_COMPANY_DATE[name]||'',7)}

function daysBetween(aStr,bStr){
  const a=new Date(aStr+'T00:00:00Z').getTime();
  const b=new Date(bStr+'T00:00:00Z').getTime();
  return Math.round((b-a)/86400000);
}

// SLA badge for the dashboard card. Returns {text, kind} or null when
// no SLA applies (already 확정, or period_start missing). 'pending' /
// 'due' / 'late' map to three color treatments in the CSS.
function slaBadge(a){
  const target=expectedFinalKst(a);
  if(!target)return null;
  const today=kstTodayString();
  const diff=daysBetween(today,target);
  if(diff>0)return {text:'확정 D-'+diff,kind:'pending'};
  if(diff===0)return {text:'확정 D-DAY',kind:'due'};
  return {text:'확정 D+'+(-diff)+' 지연',kind:'late'};
}

// Region tier for in-section ordering. Aggregates always rise:
//   0: 전국 (no country)                    — broadest aggregate
//   1: 전국_<country>                       — country-specific aggregate
//   2: specific region (e.g. 경기 수원시)   — site-level breakdown
function regionTier(a){
  if(a.region==='전국'&&!a.country)return 0;
  if(a.region==='전국')return 1;
  return 2;
}

// Human-friendly period label that bundles status into one phrase so
// the card / modal header reads naturally in Korean.
function niceLabel(a){
  const ps=a.period_start||'';
  const m=ps.slice(5,7).replace(/^0/,'');
  const dStart=ps.slice(8,10).replace(/^0/,'');
  const dEnd=(a.period_end||'').slice(8,10).replace(/^0/,'');
  const status=a.status==='final'?'확정':'잠정';
  if(a.period_kind==='monthly'&&a.status==='final')return m+'월 확정';
  if(a.period_kind==='monthly')return m+'월 전체 잠정';
  if(a.period_kind==='decadal_10')return m+'월 상순(1-10일) '+status;
  if(a.period_kind==='decadal_20')return m+'월 중순까지(1-20일) '+status;
  return m+'월 '+dStart+'-'+dEnd+'일 '+status;
}

// --- mini-card (used by BOTH views; click → modal) ---
function renderMiniCard(a){
  const where=whereLabel(a);
  const bg=(a.media&&a.media[0])?' style="background-image:url('+esc(a.media[0])+')"':'';
  const sla=slaBadge(a);
  const slaHtml=sla?'<span class="mini-sla '+sla.kind+'">'+esc(sla.text)+'</span>':'';
  // NEW chip — overlaid on the image corner. Visually pops without
  // taking text-line real estate. Latest-posted-today only.
  const newHtml=isAlertNew(a)?'<span class="mini-new">NEW</span>':'';
  return '<div class="mini-card '+a.dir+'" data-id="'+a.id+'">'+
    '<span class="dot" title="'+(a.dir==='export'?'수출':'수입')+'"></span>'+
    newHtml+
    '<div class="mini-img"'+bg+'></div>'+
    '<div class="mini-text">'+
      '<strong>'+esc(a.item)+'</strong>'+
      '<span>'+esc(where||'—')+'</span>'+
      slaHtml+
    '</div></div>';
}

// --- modal (full card view) ---
// Modal renders the clicked alert as 'primary' on top, then every
// other alert sharing the same dedup_key as 'secondary' inline below,
// newest-first. The operator scrolls to visually compare current
// 잠정/확정 against the previous report's graphs and tables side by
// side — the OCR-less path to '전번 확정 vs 이번 잠정' diffing.
function renderModalBody(a){
  const siblings=(BY_DEDUP[a.dedup_key]||[])
    .filter(x=>x.id!==a.id)
    .sort((x,y)=>(y.posted_at||'').localeCompare(x.posted_at||''));
  let html=renderModalCard(a,true);
  if(siblings.length){
    html+='<div class="modal-divider"><span>이전 발표 '+siblings.length+'건 — 비교 (클릭하면 그 시점이 위로)</span></div>';
    siblings.forEach(s=>{html+=renderModalCard(s,false)});
  }
  return html;
}

// Primary modal head also gets a small toolbar (URL copy + image save
// buttons) and three optional sections below the main card:
//   합산 ↔ 개별   bidirectional link between '+' composite items and
//                 their parts (when both exist in the corpus)
//   연관 종목      other companies that appear on alerts for the same
//                 item — discovery of peer stocks within a category
function findCompositeLinks(a){
  // Returns {asComposite: [...individual items], asPart: [...composite items]}
  // for the given alert's item.
  const out={asComposite:[], asPart:[]};
  if(a.is_composite&&a.composite_parts&&a.composite_parts.length){
    // 각 부품명의 최신 alert — BY_ITEM 인덱스(전체 ALERTS 스캔 제거).
    a.composite_parts.forEach(part=>{
      const partName=part.trim();
      if(!partName)return;
      const found=(BY_ITEM[partName]||[]).find(isLatest);
      if(found)out.asComposite.push(found);
    });
  }
  // 이 품목을 부품으로 갖는 합산 alert — COMPOSITE_BY_PART 인덱스(풀스캔 제거).
  (COMPOSITE_BY_PART[a.item]||[]).forEach(x=>{ if(isLatest(x))out.asPart.push(x); });
  return out;
}

function findPeerStocks(a){
  // Other companies mentioned on alerts of the SAME item (any
  // region/country). Dedup, drop the alert's own stocks.
  // 같은 품목 alerts 만 — BY_ITEM 인덱스(전체 ALERTS 스캔 제거).
  const own=new Set(a.stocks||[]);
  const peers={};
  (BY_ITEM[a.item]||[]).forEach(x=>{
    if(x.id===a.id)return;
    (x.stocks||[]).forEach(s=>{
      if(own.has(s))return;
      peers[s]=(peers[s]||0)+1;
    });
  });
  return Object.entries(peers).sort((x,y)=>y[1]-x[1]||x[0].localeCompare(y[0]));
}

function renderModalCard(a, primary){
  const where=whereLabel(a);
  const titleSuffix=where?' ('+where+')':'';
  const dirLabel=a.dir==='export'?'수출':'수입';
  const statusLabel=a.status==='preliminary'?'잠정':'확정';
  let stocksHtml='';
  if(primary&&a.stocks&&a.stocks.length){
    stocksHtml='<div class="stocks"><span class="label">관련종목</span>'+
      a.stocks.map(s=>'<span class="stock">'+esc(s)+stockPx(s)+'</span>').join('')+
      (a.has_etc?'<span class="stock">등</span>':'')+'</div>';
  }
  let imagesHtml='';
  if(a.media&&a.media.length){
    imagesHtml='<div class="modal-images">'+
      a.media.map(p=>'<img loading="lazy" src="'+esc(p)+'" alt="">').join('')+'</div>';
  }else{
    imagesHtml='<div class="empty">이미지 없음</div>';
  }
  const titleHtml=primary
    ? '<h2>'+esc(a.item)+esc(titleSuffix)+'</h2>'
    : '<h3 class="sib-title">📅 '+esc(niceLabel(a))+'</h3>';
  const sla=slaBadge(a);
  const slaBadgeHtml=sla?'<span class="badge sla-'+sla.kind+'">'+esc(sla.text)+'</span>':'';
  const klass=primary?'modal-card primary':'modal-card secondary';

  // Primary-only sections — toolbar + composite links + peer stocks.
  let extrasHtml='';
  if(primary){
    extrasHtml+='<div class="modal-toolbar">'+
      '<button type="button" class="tool-btn" data-tool="copy-url" data-id="'+a.id+'">🔗 URL 복사</button>'+
      '<button type="button" class="tool-btn" data-tool="save-image" data-id="'+a.id+'">🖼 이미지 저장</button>'+
      '</div>';

    const links=findCompositeLinks(a);
    if(links.asComposite.length||links.asPart.length){
      const chips=[];
      links.asComposite.forEach(p=>{
        chips.push('<button type="button" class="link-chip" data-id="'+p.id+'">→ '+esc(p.item)+'</button>');
      });
      links.asPart.forEach(p=>{
        chips.push('<button type="button" class="link-chip" data-id="'+p.id+'">⤴ '+esc(p.item)+' (합산)</button>');
      });
      extrasHtml+='<div class="modal-links"><span class="label">합산 ↔ 개별</span>'+chips.join('')+'</div>';
    }

    const peers=findPeerStocks(a);
    if(peers.length){
      const shown=peers.slice(0,8);
      const more=peers.length>shown.length?' 외 '+(peers.length-shown.length)+'개':'';
      extrasHtml+='<div class="modal-peers"><span class="label">같은 품목 다른 회사</span>'+
        shown.map(([n,c])=>'<span class="peer-chip" data-name="'+esc(n)+'" title="'+c+'건">'+esc(n)+'</span>').join('')+
        esc(more)+
        '</div>';
    }
  }

  return '<div class="'+klass+'" data-id="'+a.id+'">'+
    '<div class="modal-head">'+
      titleHtml+
      '<div class="badges">'+
        (primary?'<span class="badge '+a.dir+'">'+dirLabel+'</span>':'')+
        '<span class="badge '+a.status+'">'+statusLabel+'</span>'+
        (a.is_composite?'<span class="badge composite">합산</span>':'')+
        slaBadgeHtml+
      '</div>'+
      (primary?'<div class="period-label">📅 '+esc(niceLabel(a))+'</div>':'')+
      '<div class="sub">게시 '+esc(a.posted_at)+'</div>'+
      stocksHtml+
      extrasHtml+
    '</div>'+imagesHtml+'</div>';
}

function showModal(a){
  _openAlert=a;                 // 히스토리 lazy 로드 후 재렌더 대상
  document.getElementById('modal-body').innerHTML=renderModalBody(a);
  document.getElementById('modal').hidden=false;
  document.body.style.overflow='hidden';
  _loadHistory();               // 모달 첫 클릭 때만 siblings fetch(이후 재렌더)
}
function hideModal(){
  _openAlert=null;
  document.getElementById('modal').hidden=true;
  document.body.style.overflow='';
}

// --- state + filter ---
const state={dir:'',status:'',q:'',onlynew:''};
function matches(a){
  if(!isLatest(a))return false;  // views render the latest of each dedup_key
  if(state.dir&&a.dir!==state.dir)return false;
  if(state.status&&a.status!==state.status)return false;
  if(state.onlynew&&!isAlertNew(a))return false;  // 🆕 최근 7일 게시 카드만
  if(state.q){
    const q=state.q.toLowerCase();
    const hay=(a.item+' '+a.region+' '+a.country+' '+(a.stocks||[]).join(' ')).toLowerCase();
    if(!hay.includes(q))return false;
  }
  return true;
}

// --- view builders ---
// Section header can carry one or two muted subtitle lines under the
// title. 품목별 uses [stocks-union, region/country-count]; 회사별 uses
// just [items-count]. Empty entries are dropped silently. The optional
// `newBadge` flag puts a small NEW chip next to the title for sections
// whose item/company first appeared within the last 7 days.
function renderSection(title, subtitles, miniCardsHtml, newBadge, headerExtra){
  const lines=(subtitles||[]).filter(Boolean)
    .map(s=>'<div class="sub-line">'+esc(s)+'</div>').join('');
  const badge=newBadge?' <span class="section-new">NEW</span>':'';
  return '<section class="section">'+
    '<div class="section-header">'+
      '<h2>'+esc(title)+(headerExtra||'')+badge+'</h2>'+
      lines+
    '</div>'+
    '<div class="section-items">'+miniCardsHtml+'</div>'+
  '</section>';
}

function buildItemsView(filtered){
  // Group filtered alerts by item; **첫 등장 품목(isItemNew)을 맨 위로**
  // (사용자 2026-06-15), 그 다음 most recent posted_at 순(newest items rise);
  // within each section sort variants by recency too.
  const byItem={};
  filtered.forEach(a=>{(byItem[a.item]=byItem[a.item]||[]).push(a)});
  const sorted=Object.entries(byItem).sort((x,y)=>{
    const xNew=isItemNew(x[0]), yNew=isItemNew(y[0]);
    if(xNew!==yNew)return xNew?-1:1;          // 첫 등장 품목 최상단
    const xMax=x[1].reduce((m,a)=>(a.posted_at||'')>m?a.posted_at:m,'');
    const yMax=y[1].reduce((m,a)=>(a.posted_at||'')>m?a.posted_at:m,'');
    return yMax.localeCompare(xMax)||x[0].localeCompare(y[0]);
  });
  if(!sorted.length)return '<div class="empty">조건에 맞는 품목이 없습니다.</div>';
  return sorted.map(([name,variants])=>{
    // Within a 품목 section: 전국 aggregates first (broadest, then
    // country-specific), then site-level breakdowns. Inside each tier
    // sort by posted_at desc so the freshest report is on top.
    variants.sort((a,b)=>
      regionTier(a)-regionTier(b)||
      (b.posted_at||'').localeCompare(a.posted_at||'')
    );
    const cards=variants.map(renderMiniCard).join('');
    // Country mix bar between section header and the cards — only
    // for sections with more than one variant (single-variant
    // sections don't benefit from a mix view).
    const mixHtml=variants.length>1?countryMix(variants):'';
    return renderSection(name, [
      stocksSubtitle(variants),
      variants.length+'개 (지역/국가)',
    ], mixHtml+cards, isItemNew(name));
  }).join('');
}

// Item × country matrix view. One row per item, one column per
// country (sorted by total alerts desc), cell color = number of
// distinct alerts for that pair. Clicking a cell opens the latest
// alert for that (item, country) pair as a modal.
function buildMatrixView(filtered){
  const pivot={};
  const itemTotals={};
  const countryTotals={};
  filtered.forEach(a=>{
    const country=a.country||'전국';
    pivot[a.item]=pivot[a.item]||{};
    (pivot[a.item][country]=pivot[a.item][country]||[]).push(a);
    itemTotals[a.item]=(itemTotals[a.item]||0)+1;
    countryTotals[country]=(countryTotals[country]||0)+1;
  });
  const items=Object.keys(itemTotals).sort((x,y)=>itemTotals[y]-itemTotals[x]||x.localeCompare(y));
  const countries=Object.keys(countryTotals).sort((x,y)=>countryTotals[y]-countryTotals[x]||x.localeCompare(y));

  if(!items.length||!countries.length){
    return '<div class="empty">조건에 맞는 데이터가 없습니다.</div>';
  }

  // Cell color scales with frequency vs the section max so the
  // densest pair is fully saturated and sparse pairs fade out.
  let maxCell=1;
  items.forEach(i=>countries.forEach(c=>{const v=(pivot[i][c]||[]).length;if(v>maxCell)maxCell=v}));
  function cellColor(n){
    if(!n)return 'transparent';
    const ratio=n/maxCell;
    const op=(0.15+ratio*0.85).toFixed(2);
    return 'rgba(0,113,227,'+op+')';
  }

  const head='<thead><tr><th class="row-head">품목 \\ 국가</th>'+
    countries.map(c=>'<th class="col-head"><span>'+esc(c)+'</span><em>'+countryTotals[c]+'</em></th>').join('')+
    '</tr></thead>';
  const body='<tbody>'+items.map(i=>{
    const cells=countries.map(c=>{
      const alerts=pivot[i][c]||[];
      if(!alerts.length)return '<td class="cell empty-cell"></td>';
      const latest=alerts.sort((a,b)=>(b.posted_at||'').localeCompare(a.posted_at||''))[0];
      return '<td class="cell" data-id="'+latest.id+'" style="background:'+cellColor(alerts.length)+'">'+alerts.length+'</td>';
    }).join('');
    return '<tr><th class="row-head">'+esc(i)+' <em>'+itemTotals[i]+'</em></th>'+cells+'</tr>';
  }).join('')+'</tbody>';

  return '<div class="matrix-meta">'+items.length+'개 품목 × '+countries.length+'개 국가 (셀 = (품목·국가) 알림 수, 색 진하기 = 빈도). 셀 클릭 → 최신 alert 모달.</div>'+
    '<div class="matrix-scroll"><table class="matrix-table">'+head+body+'</table></div>';
}

// Country mix mini-chart for a 품목 section. Renders an inline SVG
// stacked bar showing how the item's variants are distributed across
// (region, country) buckets. 'no-country' entries collapse into a
// '전국' bucket; multi-country entries spread proportionally.
function countryMix(variants){
  const buckets={};
  variants.forEach(a=>{
    const label=a.country||a.region||'-';
    buckets[label]=(buckets[label]||0)+1;
  });
  const entries=Object.entries(buckets).sort((x,y)=>y[1]-x[1]);
  const total=entries.reduce((s,[,c])=>s+c,0)||1;
  // Palette — cycle through deterministically by hashing label string.
  const colors=['#0071e3','#34c759','#ff9500','#af52de','#ff3b30','#5ac8fa','#ffcc00','#ff2d55','#5856d6','#a2845e'];
  function colorFor(label){
    let h=0;for(let i=0;i<label.length;i++)h=(h*31+label.charCodeAt(i))&0xffff;
    return colors[h%colors.length];
  }
  // SVG: 100% width, 18px tall stacked segments. Each label tagged
  // with title="<label> (N)" for hover tooltip.
  let x=0;
  const segs=entries.map(([label,n])=>{
    const w=(n/total)*100;
    const seg='<rect x="'+x.toFixed(2)+'%" y="0" width="'+w.toFixed(2)+'%" height="18" fill="'+colorFor(label)+'"><title>'+esc(label)+' ('+n+')</title></rect>';
    x+=w;
    return seg;
  }).join('');
  const legendShown=entries.slice(0,5);
  const legend=legendShown.map(([label,n])=>
    '<span class="mix-legend-item"><span class="mix-dot" style="background:'+colorFor(label)+'"></span>'+esc(label)+' <em>'+n+'</em></span>'
  ).join('');
  const more=entries.length>legendShown.length?' <span class="mix-legend-more">외 '+(entries.length-legendShown.length)+'개</span>':'';
  return '<div class="country-mix">'+
    '<svg class="mix-bar" preserveAspectRatio="none" viewBox="0 0 100 18" xmlns="http://www.w3.org/2000/svg">'+segs+'</svg>'+
    '<div class="mix-legend">'+legend+more+'</div>'+
  '</div>';
}

function buildCompaniesView(filtered){
  const byCompany={};
  // 회사 섹션은 companies(품목·제품명 거른 회사명)로 그룹핑 — 원본 stocks엔 품목
  // 설명이 섞여 가짜 회사 섹션이 생기던 문제. companies 없으면 stocks로 폴백.
  filtered.forEach(a=>{(a.companies||a.stocks||[]).forEach(s=>{(byCompany[s]=byCompany[s]||[]).push(a)})});
  let sorted=Object.entries(byCompany).sort((x,y)=>y[1].length-x[1].length||x[0].localeCompare(y[0]));
  // RULE: in 회사별 view, if the search query matches one or more
  // company names directly, narrow to those sections. Otherwise keep
  // the existing alert-content match behaviour for queries like '라면'.
  if(state.q){
    const q=state.q.toLowerCase();
    const direct=sorted.filter(([n])=>n.toLowerCase().includes(q));
    if(direct.length)sorted=direct;
  }
  if(!sorted.length)return '<div class="empty">조건에 맞는 회사가 없습니다.</div>';
  return sorted.map(([name,items])=>{
    // Within a 회사 section: keep same-item rows clustered (so '라면'
    // entries stay together, '디램' together, ...), then inside each
    // item cluster apply the same 전국-tier + posted_at-desc rule as
    // the 품목별 view.
    items.sort((a,b)=>
      (a.item||'').localeCompare(b.item||'')||
      regionTier(a)-regionTier(b)||
      (b.posted_at||'').localeCompare(a.posted_at||'')
    );
    const cards=items.map(renderMiniCard).join('');
    // 회사명=종목 → 헤더에 그 회사 EOD 가격(있을 때). 모달 칩과 둘 다 유지.
    return renderSection(name, [items.length+'개 품목'], cards,
                         isCompanyNew(name), sectionStockPx(name));
  }).join('');
}

function renderHeaderMeta(){
  const st=currentDataStatus();
  const ms=document.getElementById('meta-status');
  const sp=[];
  if(st.prelim)sp.push('잠정 <strong>'+esc(latestPeriodStr(st.prelim))+'</strong>');
  if(st.final)sp.push('확정 <strong>'+esc(latestPeriodStr(st.final))+'</strong>');
  ms.innerHTML=sp.length?'📊 현재 '+sp.join(' · '):'';
  const next=nextAnnouncement();
  const mn=document.getElementById('meta-next');
  if(next){
    mn.innerHTML='⏭ 다음 발표 <strong>D-'+next.daysUntil+'</strong> ('+esc(next.date)+' · '+esc(next.kind)+')';
  }else{
    mn.textContent='';
  }
  const q=quickStats();
  const mt=document.getElementById('meta-today');
  if(q.newToday>0){
    const parts=['오늘 신규 <strong>'+q.newToday+'</strong>건'];
    if(q.finalsToday)parts.push('확정 도착 <strong>'+q.finalsToday+'</strong>');
    if(q.firstItemsToday)parts.push('첫 등장 품목 <strong>'+q.firstItemsToday+'</strong>');
    mt.innerHTML='📌 '+parts.join(' · ');
  }else{
    mt.textContent='';
  }
}

// --- lazy view rendering (2026-06-15) ---
// 옛 render() 는 3개 클라이언트 뷰(품목별/회사별/매트릭스)를 매 필터·검색
// 마다 innerHTML 로 전부 빌드 → 카드 ~1054개 × 3 = DOM 3000+ 노드를 항상
// 보유했다. 모달 클릭(showModal 의 innerHTML)·검색이 그 무게에 깔려 느렸음
// (사용자 2026-06-15 '카드 클릭 너무 오래'). 이제 활성 탭만 빌드하고 나머지
// 두 뷰는 '더티' 표시 후 탭 전환 시 빌드 → DOM 1×1054 로 1/3. CSV·모달은
// ALERTS/ALERT_BY_ID(데이터)를 직접 읽어 DOM 무관이라 lazy 해도 안전.
const _CLIENT_VIEWS={items:buildItemsView,companies:buildCompaniesView,matrix:buildMatrixView};
let _viewDirty={items:true,companies:true,matrix:true};
// 별도파일 lazy fetch (2026-06-16 '느려') — 산업트렌드(588 SVG ~7MB)를 index.html
// 인라인 대신 industry_panel.html 로 빼 탭 첫 열 때만 fetch. 초기 로딩 11MB→~3MB.
// data-src 없는 뷰(인라인/클라이언트)는 무시. 실패 시 재시도 가능하게 loaded 해제.
function _lazyFetchView(view){
  if(!view||!view.dataset.src||view.dataset.loaded) return;
  view.dataset.loaded='1';
  fetch(view.dataset.src,{cache:'no-store'})
    .then(function(r){if(!r.ok)throw 0;return r.text();})
    .then(function(html){
      view.innerHTML=html;
      // innerHTML 은 <script> 미실행 → 프래그먼트의 스크립트(window._scrollRaw
      // 정의·호출, 월별 원자료 우측 디폴트 스크롤 등) 재실행해야 동작(사용자
      // 2026-06-16 '스크롤 디폴트 오른쪽 풀림' — #455 lazy 전환 회귀). 1회만(loaded gate).
      view.querySelectorAll('script').forEach(function(old){
        const s=document.createElement('script');
        if(old.src){s.src=old.src;}else{s.textContent=old.textContent;}
        old.parentNode.replaceChild(s,old);
      });
      if(window._scrollRaw)window._scrollRaw(view);   // 월별 원자료 → 최신(우측)
    })
    .catch(function(){view.dataset.loaded='';
      view.innerHTML='<div class="lazy-load">산업트렌드 불러오기 실패 — 다시 눌러 주세요.</div>';});
}
let _lastFiltered=[];
function _activeTab(){
  const a=document.querySelector('.tab.active');
  return a?a.dataset.tab:'items';
}
function _buildView(name){
  const fn=_CLIENT_VIEWS[name];
  if(!fn)return;                       // industry/heatmap = 서버 렌더, 클라 빌드 없음
  const el=document.getElementById(name+'-view');
  if(el)el.innerHTML=fn(_lastFiltered);
  _viewDirty[name]=false;
}
function render(){
  _lastFiltered=ALERTS.filter(matches);
  document.getElementById('visible-count').textContent=_lastFiltered.length;
  // 클라 3뷰 전부 더티 표시 → 활성 뷰만 즉시 빌드, 나머지는 탭 전환 때.
  _viewDirty={items:true,companies:true,matrix:true};
  _buildView(_activeTab());                           // industry/heatmap 이면 no-op (정상)
  filterIndustryCards();                              // 산업트렌드 탭도 검색 (2026-06-12)
  if(window.hmFilter) window.hmFilter(state.q||'');   // 히트맵 탭도 검색
  renderHeaderMeta();
}
function filterIndustryCards(){
  // 산업 카드(.ind-card) 텍스트 매칭 — 속보/신호/배너는 공통 컨텍스트라 유지
  const q=(state.q||'').toLowerCase();
  document.querySelectorAll('#industry-view .ind-card').forEach(function(c){
    c.style.display=(!q||c.textContent.toLowerCase().indexOf(q)>=0)?'':'none';
  });
}

// --- 산업트렌드 월별/TTM toggle (delegated; cards are server-rendered) ---
// 스코프 = .ind-row (카드 본체 단위). .ind-card 아님 — 품목 랭킹표 행클릭
// 확장 카드는 <table> 안이라 .ind-card 가 없어 토글이 죽었었음(2026-06-13).
// 그리고 카드당 패널이 cell1(좌·수출액)+cell2(우·YoY) 2쌍이라 querySelectorAll
// 로 둘 다 swap — 단수 querySelector 면 우측 차트가 안 바뀌던 버그.
document.addEventListener('click',function(e){
  const b=e.target.closest('.ind-tg-btn');
  if(!b||b.classList.contains('ind-dir-btn'))return;   // 방향 토글은 별도 핸들러
  const row=b.closest('.ind-row');
  if(!row)return;
  const view=b.dataset.indView;
  row.querySelectorAll('.ind-tg-btn').forEach(x=>x.classList.toggle('is-active',x===b));
  row.querySelectorAll('.ind-monthly').forEach(mo=>mo.hidden=(view!=='monthly'));
  row.querySelectorAll('.ind-ttm').forEach(tt=>tt.hidden=(view!=='ttm'));
});

// --- 산업트렌드 수출/수입 방향 토글 (2026-06-13 — 수입 카드 전체셋) ---
document.addEventListener('click',function(e){
  const b=e.target.closest('.ind-dir-btn');
  if(!b)return;
  const mode=b.dataset.indDir;   // all(기본: 수출→구분선→수입) | exp | imp
  document.querySelectorAll('.ind-dir-btn').forEach(x=>x.classList.toggle('is-active',x===b));
  document.querySelectorAll('.ind-dirset').forEach(d=>{
    d.style.display=(mode==='all'||d.dataset.dir===mode)?'':'none';
  });
  document.querySelectorAll('.ind-dir-divider').forEach(d=>{
    d.style.display=(mode==='all')?'':'none';
  });
  filterIndustryCards();   // 검색어 유지 — 새로 보인 방향에도 적용
});

// --- MTI 랭킹 행 클릭 → 풀 카드 상세 토글 (2026-06-13) ---
document.addEventListener('click',function(e){
  const tr=e.target.closest('tr.ind-mti-row');
  if(!tr)return;
  const d=tr.nextElementSibling;
  if(d&&d.classList.contains('ind-mti-d')){
    d.hidden=!d.hidden;
    const m=tr.querySelector('.ind-mti-more');
    if(m)m.textContent=d.hidden?'▸':'▾';
    // 펼치면 그 카드를 전폭으로(반대편 빈공간까지) → 상세가 기본 카드 너비로, 접으면
    // 원복 (사용자 2026-06-20). 카드에 열린 상세가 하나라도 있으면 ind-open.
    const card=tr.closest('.ind-sub-card');
    if(card)card.classList.toggle('ind-open',!!card.querySelector('.ind-mti-d:not([hidden])'));
    // 펼친 상세의 월별 원자료표를 최신(우측)으로. ind-open 전폭 전환은 큰 reflow라
    // 스크롤 계산이 그 전에 잡히면 좌측에 남음(사용자 2026-06-20 '개별 스크롤 풀림').
    // offsetWidth 읽어 reflow 강제 flush 후 _scrollRaw(이중rAF+백업) → 우측 고정.
    if(!d.hidden){
      if(card)void card.offsetWidth;
      if(window._scrollRaw)window._scrollRaw(d);
    }
  }
});

// --- 잠정 모멘텀 정렬 토글 (절대액/모멘텀/|YoY|) — 4 테이블 동시 재정렬 ---
document.addEventListener('click',function(e){
  const b=e.target.closest('.ind-prov-sort-btn');
  if(!b)return;
  const panel=b.closest('.ind-prov-mom'); if(!panel)return;
  const mode=b.dataset.sort;
  panel.querySelectorAll('.ind-prov-sort-btn').forEach(x=>x.classList.toggle('is-active',x===b));
  panel.querySelectorAll('.ind-prov-tbl').forEach(tbl=>{
    const tb=tbl.querySelector('tbody'); if(!tb)return;
    const rows=Array.from(tb.querySelectorAll('tr'));
    const pinned=rows.filter(r=>r.dataset.pin==='1');
    const rest=rows.filter(r=>r.dataset.pin!=='1');
    rest.sort((a,b)=>{
      const av=parseFloat(a.dataset[mode]); const bv=parseFloat(b.dataset[mode]);
      const ax=isNaN(av)?-Infinity:av; const bx=isNaN(bv)?-Infinity:bv;
      return bx-ax;  // 내림차순
    });
    [...pinned,...rest].forEach(r=>tb.appendChild(r));
  });
});

// --- tab / chip / search / modal events (delegated) ---
document.querySelectorAll('.tab').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    btn.classList.add('active');
    const tab=btn.dataset.tab;
    const view=document.getElementById(tab+'-view');
    view.classList.add('active');
    if(_CLIENT_VIEWS[tab]&&_viewDirty[tab])_buildView(tab);   // lazy: 더티면 지금 빌드
    _lazyFetchView(view);   // 산업트렌드 등 별도파일(data-src) 탭 — 첫 열 때 fetch(초기 11MB→3MB)
    // 탭이 보이면(레이아웃 생김) 월별 원자료를 우측(최신) 끝으로 (사용자 2026-06-15)
    if(window._scrollRaw)window._scrollRaw(view);
  });
});
document.querySelectorAll('.chip').forEach(chip=>{
  chip.addEventListener('click',()=>{
    const g=chip.closest('.chip-group');
    g.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
    chip.classList.add('active');
    state[g.dataset.key]=chip.dataset.val;
    render();
  });
});
let qTimer;
document.getElementById('q').addEventListener('input',e=>{
  clearTimeout(qTimer);
  qTimer=setTimeout(()=>{state.q=e.target.value;render();},120);
});
function alertShareUrl(a){
  // Hash-based deep link: #a/<id>. Hashes never round-trip to the
  // server so this works even behind BasicAuth.
  return location.origin+location.pathname+'#a/'+a.id;
}

async function copyToClipboard(text){
  try{
    await navigator.clipboard.writeText(text);
    return true;
  }catch(_){
    // Fallback for older browsers / insecure contexts.
    const ta=document.createElement('textarea');
    ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    try{document.execCommand('copy');return true}finally{document.body.removeChild(ta)}
  }
}

function flashTool(btn,msg){
  const orig=btn.textContent;btn.textContent=msg;btn.disabled=true;
  setTimeout(()=>{btn.textContent=orig;btn.disabled=false},1500);
}

document.addEventListener('click',e=>{
  // Composite link chips inside the modal — open the linked alert
  // as the new primary. Handled before .modal-card.secondary because
  // they live inside it.
  const link=e.target.closest('.link-chip');
  if(link&&link.dataset.id){
    const id=parseInt(link.dataset.id,10);
    const a=ALERT_BY_ID.get(id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  // Peer-stock chip → close modal, search by company, switch to
  // 회사별 tab (smart-search narrows to that company section).
  const peer=e.target.closest('.peer-chip');
  if(peer&&peer.dataset.name){
    state.q=peer.dataset.name;
    document.getElementById('q').value=peer.dataset.name;
    document.querySelectorAll('.tab').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    document.querySelector('[data-tab="companies"]').classList.add('active');
    document.getElementById('companies-view').classList.add('active');
    render();
    hideModal();
    return;
  }
  // Toolbar buttons inside the modal: copy URL / save image.
  const tool=e.target.closest('.tool-btn');
  if(tool&&tool.dataset.tool){
    const id=parseInt(tool.dataset.id,10);
    const a=ALERT_BY_ID.get(id);
    if(!a)return;
    if(tool.dataset.tool==='copy-url'){
      copyToClipboard(alertShareUrl(a)).then(ok=>flashTool(tool,ok?'복사됨 ✓':'복사 실패'));
    }else if(tool.dataset.tool==='save-image'){
      const url=(a.media&&a.media[0])?absUrl(a.media[0]):null;
      if(!url){flashTool(tool,'이미지 없음');return}
      const link=document.createElement('a');
      link.href=url;link.download='trade-'+a.id+'.jpg';link.target='_blank';
      document.body.appendChild(link);link.click();document.body.removeChild(link);
    }
    return;
  }
  const card=e.target.closest('.mini-card');
  if(card&&card.dataset.id){
    const id=parseInt(card.dataset.id,10);
    const a=ALERT_BY_ID.get(id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  // Matrix cell click → open the latest alert for that (item, country)
  const cell=e.target.closest('.matrix-table .cell');
  if(cell&&cell.dataset.id){
    const id=parseInt(cell.dataset.id,10);
    const a=ALERT_BY_ID.get(id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  // Clicking a secondary card inside the modal swaps it to primary,
  // so the operator can drill deeper into the history (e.g. compare
  // 4월 확정 → 3월 확정 → ...) without closing and reopening.
  const sib=e.target.closest('.modal-card.secondary');
  if(sib&&sib.dataset.id){
    const id=parseInt(sib.dataset.id,10);
    const a=ALERT_BY_ID.get(id);
    if(a){document.getElementById('modal-body').scrollTop=0;showModal(a)}
    return;
  }
  if(e.target.classList.contains('modal-close')||e.target.classList.contains('modal-backdrop')){
    hideModal();
  }
});

// Deep-link support: opening 'http://.../dashboard/#a/123' auto-opens
// the modal for alert 123 once the page has finished initial render.
function handleHashDeepLink(){
  const m=location.hash.match(/^#a\/(\d+)$/);
  if(!m)return;
  const id=parseInt(m[1],10);
  const a=ALERT_BY_ID.get(id);
  if(a)showModal(a);
}
window.addEventListener('hashchange',handleHashDeepLink);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'&&!document.getElementById('modal').hidden)hideModal();
});

// --- CSV download (current filter) ---
// Generates the CSV client-side so there's no server round-trip; the
// '﻿' BOM keeps Korean readable when Excel opens the file. Only
// the LATEST-visible-after-filter rows are exported so the download
// matches what the operator currently sees on screen.
//
// Full field set includes everything in the alert payload plus two
// computed SLA columns and absolute media URLs (so Excel cells turn
// into clickable hyperlinks instead of unreachable relative paths).
function absUrl(p){
  try{return new URL(p, location.href).href}catch(_){return p}
}

function metaPairsToString(obj){
  if(!obj)return '';
  return Object.entries(obj).map(([k,v])=>k+'='+v).join(';');
}

function downloadCSV(){
  const filtered=ALERTS.filter(matches);
  const today=kstTodayString();
  const headers=[
    'id','dedup_key','direction','status','title_kind',
    'item','item_raw','is_composite','composite_parts',
    'region','country','regions','countries',
    'stocks','stocks_meta','has_etc',
    'period_start','period_end','period_kind',
    'expected_final_date','days_to_final',
    'posted_at','ingested_at',
    'commentary','parse_warnings','media_urls',
  ];
  const rows=[headers];
  filtered.forEach(a=>{
    const expectedFinal=expectedFinalKst(a);
    const days=expectedFinal?daysBetween(today,expectedFinal):'';
    rows.push([
      a.id,a.dedup_key,a.dir,a.status,a.title_kind,
      a.item,a.item_raw,a.is_composite?1:0,(a.composite_parts||[]).join(';'),
      a.region,a.country,(a.regions||[]).join(';'),(a.countries||[]).join(';'),
      (a.stocks||[]).join(';'),metaPairsToString(a.stocks_meta),a.has_etc?1:0,
      a.period_start,a.period_end,a.period_kind,
      expectedFinal||'',days,
      a.posted_at,a.ingested_at,
      a.commentary||'',(a.warnings||[]).join(';'),
      (a.media||[]).map(absUrl).join(';'),
    ]);
  });
  const csv=rows.map(r=>r.map(v=>{
    const s=v==null?'':String(v);
    if(/[",\n]/.test(s))return '"'+s.replace(/"/g,'""')+'"';
    return s;
  }).join(',')).join('\n');
  const blob=new Blob(['﻿'+csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;
  link.download='trade-alerts-'+today+'.csv';
  document.body.appendChild(link);link.click();document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
document.getElementById('csv-btn').addEventListener('click',function(){
  // 활성 탭별 CSV (사용자 2026-06-13) — 산업트렌드/히트맵도 내보내기
  const active=document.querySelector('.tab.active');
  const tab=active?active.dataset.tab:'items';
  if(tab==='industry'){ downloadIndustryCSV(); return; }
  if(tab==='heatmap'&&window.hmCSV){ downloadRowsCSV(window.hmCSV(),'heatmap'); return; }
  downloadCSV();
});
function downloadRowsCSV(rows,stem){
  const csv='\ufeff'+rows.map(r=>r.map(v=>{
    const s=String(v==null?'':v);
    return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;
  }).join(',')).join('\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  a.download=stem+'_'+kstTodayString()+'.csv';
  document.body.appendChild(a);a.click();a.remove();
}
function downloadIndustryCSV(){
  // 산업 탭 = 품목(MTI) export 2종 (사용자 2026-06-13 '이런것들 엑셀로'):
  // 요약(품목당 1행, 카드 지표) + 월별 원시. 헤더는 industry.MTI_*_HEADER
  // 와 동기(테스트가 가드). 산업레벨(ind-csv-data)은 품목 monthly 의 산업
  // 컬럼으로 피벗 가능해 별도 버튼 불필요.
  const sEl=document.getElementById('mti-csv-summary');
  const mEl=document.getElementById('mti-csv-monthly');
  if(!sEl||!mEl){alert('품목 데이터가 아직 없습니다');return;}
  // 헤더는 industry.MTI_SUMMARY_HEADER/MTI_MONTHLY_HEADER 와 동기(테스트 가드).
  // 공통 메타 + 수출 10지표 + 수입 10지표 (급등률=MoM%·급증액=MoMΔ$ 포함).
  const dirCols=['$','MoM%(급등률)','MoMΔ$(급증액)','YoY%','ΔYoY%p','3개월평균YoY%','12M MA$','MA대비%','단가$/kg','전년동월단가$/kg'];
  const sHead=['품목','MTI','산업','최신월','구성HS','관련기업(큐레이션)','관련기업(채널)']
    .concat(dirCols.map(function(c){return '수출'+c;}))
    .concat(dirCols.map(function(c){return '수입'+c;}));
  const mHead=['품목','MTI','산업','월','수출$','수입$','수출중량kg','수입중량kg','수출단가$/kg','수입단가$/kg'];
  downloadRowsCSV([sHead].concat(JSON.parse(sEl.textContent)),'품목_요약');
  // 2번째 다운로드는 살짝 지연 — 일부 브라우저가 연속 다운로드를 막음.
  setTimeout(function(){downloadRowsCSV([mHead].concat(JSON.parse(mEl.textContent)),'품목_월별');},350);
}

// --- automatic dark mode 19:00 - 07:00 KST ---
// Computes KST hour from UTC + 9 so the page works regardless of the
// viewer's local timezone. Re-checks every 60 s so the page transitions
// without a reload at 7am / 7pm KST.
function applyDarkMode(){
  const h=(new Date().getUTCHours()+9)%24;
  document.body.classList.toggle('dark', h>=19||h<7);
}
applyDarkMode();
setInterval(applyDarkMode,60000);

render();
// Fire after initial render so the modal can locate by id.
handleHashDeepLink();

// 산업트렌드(무거운 ~7MB 프래그먼트)는 탭 클릭 시 fetch 라 첫 클릭이 느림(사용자
// 2026-06-16 '갭 두고 자동 로드'). 초기 렌더(경량 index) 끝난 뒤 브라우저 idle
// (최대 3초) 시 백그라운드로 미리 prefetch → 탭 클릭 시 즉시 표시(따로 안 눌러도).
// _lazyFetchView 의 loaded 가드로 클릭과 중복 fetch 안 되고, idle 후라 첫 화면
// 속도엔 영향 없음(critical paint 와 비경쟁). requestIdleCallback 없으면 2.5초 폴백.
(function(){
  function _prefetchLazy(){
    document.querySelectorAll('.view[data-src]').forEach(function(v){_lazyFetchView(v);});
  }
  if(window.requestIdleCallback) requestIdleCallback(_prefetchLazy,{timeout:3000});
  else setTimeout(_prefetchLazy,2500);
})();
"""


def main() -> int:
    default_data = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
    ap = argparse.ArgumentParser(
        description="Render the trade-bot dashboard from store.db."
    )
    ap.add_argument(
        "--db", type=Path, default=default_data / "store.db",
        help="path to store.db",
    )
    ap.add_argument(
        "--out", type=Path, default=default_data / "dashboard" / "index.html",
        help="output HTML path",
    )
    ap.add_argument(
        "--media-url", type=str, default="../",
        help="prefix prepended to each media path (default '../' suits "
             "~/.trade/dashboard/index.html browsing the local file)",
    )
    ap.add_argument(
        "--eval-miss-path",
        type=Path,
        default=default_data / "eval_misses.jsonl",
        help=(
            "Path to eval_misses.jsonl (unstored_check's backlog of "
            "un-parseable BeOn captions). When the file exists and "
            "is non-empty, a header line shows the count + age of the "
            "oldest entry. Missing / empty file → no line rendered."
        ),
    )
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(
        args.db,
        media_url_prefix=args.media_url,
        eval_miss_path=args.eval_miss_path,
        # customs_db_path / hs_map_path default to None → render_html uses
        # the module defaults (~/.trade/customs.db, ~/.trade/hs_map.tsv).
        # 산업트렌드(무거운 588 SVG)는 index.html 옆 별도 파일로 빼 탭 열 때 lazy
        # fetch → 초기 로딩 11MB→~3MB (사용자 2026-06-16 '느려'). 같은 디렉토리라
        # 정적 서버가 그대로 서빙(상대 fetch). 미생성 시 인라인 폴백.
        industry_out=args.out.parent / "industry_panel.html",
        # 모달 히스토리(siblings, 3036행 ~2.3MB)도 별도 파일로 빼 모달 첫 클릭 때만
        # fetch → 초기 ALERTS 인라인 3.17MB→~0.8MB(최신만) (사용자 2026-06-16
        # '최신만 인라인 + 모달 히스토리 on-demand'). 뷰/검색/CSV 는 최신만 쓰므로
        # 무영향, 리스크는 모달 siblings 한 곳(graceful degrade).
        history_out=args.out.parent / "alerts_history.json",
    )
    args.out.write_text(html, encoding="utf-8")
    size_kb = len(html.encode("utf-8")) / 1024
    print(f"wrote {args.out} ({size_kb:.0f} KB)")
    # 산업트렌드 탭의 🗄 아카이브 + 📥 공유 링크가 404 안 나게 보장 + 현재 확정월
    # 동결본을 라이브와 함께 갱신(같은 달이면 차트·뷰 수정이 아카이브에도 반영;
    # 메타·과거달은 보존). 저장 카드 재사용이라 LLM 0콜.
    try:
        import json as _json
        from trade import customs as _customs, industry_archive, insights
        industry_archive.ensure_exists()
        with _customs.session() as conn:
            cards = _json.loads(insights.get_state(conn, "llm_insight_cards") or "[]")
            cards = cards if isinstance(cards, list) else []
            industry_archive.write_export(conn, cards)        # 공유 스냅샷(현재월)
            industry_archive.record_snapshot(conn, cards)     # 아카이브 현재월 = 라이브 추적
            industry_archive.regenerate()                     # 색인 재생성
    except Exception:
        pass
    # 🟢 잠정 타임라인 — 저장된 잠정 스냅샷에서 매 렌더마다 재빌드(API 0콜).
    # fetch가 아니라 렌더가 아카이브 HTML을 소유 → 렌더 코드 변경이 ~5분 내
    # 반영(6h stale-fetch 대기 불필요). 데이터 없으면 빈 색인 보장만.
    try:
        from trade import provisional_archive, customs as _customs2
        with _customs2.session() as conn:
            provisional_archive.refresh(conn)
    except Exception:
        pass
    # 🤖 AI 보고서 아카이브 색인 — 링크 404 방지 + 색인 템플릿 변경 소급 반영
    # (jsonl 적립은 dashboard_server 의 유료 render_llm 이 즉시 append+regenerate).
    try:
        from trade import report_archive
        report_archive.ensure_exists()
        report_archive.regenerate()
    except Exception:
        pass
    # 📖 품목 레퍼런스북 — 품목↔HS↔산업↔관련기업 (정적 연계표라 매 렌더 재생성 저렴).
    try:
        from trade import reference_book
        reference_book.regenerate()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
