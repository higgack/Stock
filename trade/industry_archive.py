"""산업트렌드 월별 아카이브 — 그 시점 산업트렌드 뷰 '전체'를 동결 저장.

운영자 결정: 텍스트 요약이 아니라 산업트렌드 탭 전체(🔍신호 + 분류 요약보드
+ 산업/하위품목 카드 + SVG 차트 + TTM + 월별 원자료표)를 그 달 모습 그대로
독립 self-contained 페이지로 '동결(freeze)'하고, 아카이브 색인에서 월별로
링크하는 Wayback 방식.

데이터 흐름:
  refresh_signals (데이터 변동 틱, ~월1회)
    → record_snapshot(conn, cards):
        1) 그 시점 산업트렌드 HTML 전체를 dashboard._CSS로 감싼 독립 페이지로
           ~/.trade/dashboard/archive/{확정월}.html 에 동결 저장
        2) 색인 1건(캡션+링크)을 industry_archive.jsonl 에 append/replace
    → regenerate(): jsonl → 날짜 그룹 색인 페이지(industry_archive.html)
  산업트렌드 탭 '🗄 월별 아카이브' → 색인 → 월 카드 클릭 → 그 달 동결 페이지.

동결 페이지는 dashboard._CSS(.ind-*/.ins-* 포함)를 그대로 임베드하고, 월별/
TTM 토글 + 다크모드 JS만 따로 싣는다(대시보드 전체 _JS는 필터/모달/탭 요소를
참조해 standalone에선 throw하므로 미사용).
"""
from __future__ import annotations

import html as _html
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trade import industry, llm_insights
from trade.archive_template import FieldMap, Stat, render_archive_page
from trade.dashboard import _CSS as _DASH_CSS

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")
ARCHIVE_JSONL = _DATA_DIR / "industry_archive.jsonl"
ARCHIVE_HTML = _DATA_DIR / "dashboard" / "industry_archive.html"
SNAP_DIR = _DATA_DIR / "dashboard" / "archive"          # 동결 페이지 디렉토리
SHARE_DIR = _DATA_DIR / "dashboard" / "share"           # 공개 월별 스냅샷(서빙)
_PUBLIC_TOKEN_PATH = _DATA_DIR / ".public_token"


def _public_token() -> str | None:
    token = (os.environ.get("TRADE_DASHBOARD_PUBLIC_TOKEN") or "").strip()
    if token:
        return token
    try:
        token = _PUBLIC_TOKEN_PATH.read_text().strip()
    except OSError:
        return None
    return token or None


def latest_stored_ym(conn) -> str | None:
    """저장된 산업 데이터의 최신 확정월('YYYY-MM'). 없으면 None."""
    by_ind = industry.load_stored(conn)
    if not by_ind:
        return None
    ym = ""
    for pts in industry.industry_series(by_ind).values():
        if pts and pts[-1]["ym"] > ym:
            ym = pts[-1]["ym"]
    return ym or None


def public_share_url(ym: str | None, host_hint: str | None = None) -> str | None:
    """공개 스냅샷 URL(인증 없음) — 그 확정월 시점 동결본. 토큰
    (~/.trade/.public_token 또는 env) + TRADE_PUBLIC_HOST + 월. 하나라도
    없으면 None. 외부 공유의 핵심: URL을 모르면 못 보고, 공개 prefix 안에서도
    'YYYY-MM.html' 월별 스냅샷만 서빙된다(라이브 대쉬보드 비노출)."""
    token = _public_token()
    if not token or not ym:
        return None
    host = (host_hint or os.environ.get("TRADE_PUBLIC_HOST") or "").strip().rstrip("/")
    if not host:
        return None
    return f"{host}/share/{token}/{ym}.html"

_FM = FieldMap(date="_date", ts="ts", body="body", title="title",
               cost="cost_krw", elapsed=None, kind="kind")

# 산업트렌드 월별/TTM 토글 + 다크모드(KST 19-07h) — dashboard.py 동일 로직 미러.
# 동결 페이지는 대시보드 전체 _JS(필터·모달·탭 의존)를 못 쓰므로 이 두 블록만.
_STANDALONE_JS = """
document.addEventListener('click',function(e){
  var b=e.target.closest('.ind-tg-btn'); if(!b)return;
  var card=b.closest('.ind-card'); if(!card)return;
  var view=b.dataset.indView;
  card.querySelectorAll('.ind-tg-btn').forEach(function(x){x.classList.toggle('is-active',x===b);});
  var mo=card.querySelector('.ind-monthly'), tt=card.querySelector('.ind-ttm');
  if(mo)mo.hidden=(view!=='monthly'); if(tt)tt.hidden=(view!=='ttm');
});
function applyDarkMode(){var h=(new Date().getUTCHours()+9)%24;document.body.classList.toggle('dark',h>=19||h<7);}
applyDarkMode(); setInterval(applyDarkMode,60000);
"""

_GROUP_EMOJI = {"초고성장/강세": "🚀", "턴어라운드 후보": "🔄", "부진/재하락": "🔻"}


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
    """확정월 key로 idempotent — 같은 확정월 재기록 시 교체."""
    runs = [r for r in load_runs() if r.get("key") != rec["key"]]
    runs.append(rec)
    runs.sort(key=lambda r: r.get("ts", ""))
    ARCHIVE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with ARCHIVE_JSONL.open("w", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def render_standalone(inner_html: str, *, latest: str,
                      title: str | None = None,
                      nav_links: str = "", subnote: str = "") -> str:
    """산업트렌드 HTML 전체를 dashboard CSS로 감싼 독립 페이지.

    nav_links — 헤더 우측 HTML(동결 아카이브용 back-link 등). 빈 문자열이면
                링크 미표시(공유 export용 — 받는 사람에게 404 안 나게).
    subnote   — 제목 아래 회색 한 줄(출처/단위/생성일 등, 공유용)."""
    title = title or f"산업트렌드 · {latest} 확정 스냅샷"
    right = (f"<div style=\"font-size:13px\">{nav_links}</div>"
             if nav_links else "")
    sub = (f"<div style=\"font-size:12px;color:var(--text-sub);"
           f"padding:0 16px 6px\">{subnote}</div>" if subnote else "")
    head = (
        "<div style=\"padding:14px 16px 6px;border-bottom:1px solid var(--border);"
        "display:flex;justify-content:space-between;align-items:center;"
        "flex-wrap:wrap;gap:8px\">"
        f"<div style=\"font-size:18px;font-weight:700\">📈 {_html.escape(title)}</div>"
        + right + "</div>" + sub
    )
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_html.escape(title)}</title>"
        f"<style>{_DASH_CSS}</style></head><body>"
        + head
        + f"<main class='view' style='display:block'>{inner_html}</main>"
        + f"<script>{_STANDALONE_JS}</script></body></html>"
    )


_ARCHIVE_NAV = (
    "<a href=\"../industry_archive.html\" style=\"color:var(--accent);"
    "text-decoration:none\">← 아카이브 색인</a> · "
    "<a href=\"../index.html\" style=\"color:var(--accent);text-decoration:none\">"
    "대시보드</a>"
)


def build_industry_inner(conn, cards) -> tuple[str, str]:
    """(latest_ym, 산업트렌드 전체 HTML inner). 동결·export 공용 — 🔍박스 +
    분류보드 + 산업/하위품목 카드 + 차트 전부. 데이터 없으면 ('', '')."""
    by_ind = industry.load_stored(conn)
    if not by_ind:
        return "", ""
    by_imp = industry.load_stored_imports(conn)
    by_mti = industry.load_mti_stored(conn)
    by_mti_imp = industry.load_mti_imports(conn)
    series = industry.industry_series(by_ind)
    latest = ""
    for pts in series.values():
        if pts and pts[-1]["ym"] > latest:
            latest = pts[-1]["ym"]
    inner = (llm_insights.render_html(cards or [])
             + industry.render_industry_html(by_ind, by_imp, by_mti, by_mti_imp))
    return latest, inner


def render_export(conn, cards, *, now=None) -> str | None:
    """지인에게 보낼 공유용 단일 self-contained HTML. 깨지는 nav 없음 +
    제목/출처/단위/생성일 한 줄. 데이터 없으면 None. (LLM 호출 없음 — 전달된
    cards 그대로 사용)."""
    latest, inner = build_industry_inner(conn, cards)
    if not latest:
        return None
    gen = (now or datetime.now(_KST)).date().isoformat()
    subnote = (f"관세청 확정치 {latest} 기준 · HSK-MTI 연계표 · "
               f"금액 단위 억 달러(1억$ = $100M) · 생성 {gen}")
    return render_standalone(
        inner, latest=latest,
        title=f"한국 수출 산업트렌드 · {latest} 확정",
        nav_links="", subnote=subnote)


def _pct(v, suf="%") -> str:
    return "—" if v is None else f"{v:+.0f}{suf}"


def _summary_text(by_ind, by_imp, cards) -> str:
    """색인 카드 본문 = 그 달 '전체 텍스트 내용'(검색 인덱스). 모든 산업의
    분류·YoY + 수입급증 + 🔍신호 전문을 텔레그램풍 HTML로. 동결 페이지는
    시각, 이 본문은 검색용 — 일년치가 쌓여도 '반도체' 검색이 의미를 갖게."""
    series = industry.industry_series(by_ind) if by_ind else {}
    imp_series = industry.industry_series(by_imp) if by_imp else {}
    buckets: dict[str, list] = {}
    for ind, pts in series.items():
        if pts:
            buckets.setdefault(industry.classify(pts), []).append((ind, pts[-1]))

    blocks: list[str] = []
    for label in ("초고성장/강세", "턴어라운드 후보", "부진/재하락"):
        items = buckets.get(label) or []
        if not items:
            continue
        items.sort(key=lambda t: (t[1].get("yoy") if t[1].get("yoy") is not None
                                  else -9e9), reverse=True)
        chips = " · ".join(f"{i} {_pct(p.get('yoy'))}" for i, p in items)   # 전체
        blocks.append(f"<b>{_GROUP_EMOJI.get(label, '')} {label}</b>\n{chips}")

    imp = sorted([(i, ip[-1].get("yoy")) for i, ip in imp_series.items()
                  if ip and ip[-1].get("yoy") is not None and ip[-1]["yoy"] >= 20.0],
                 key=lambda t: t[1], reverse=True)
    if imp:
        blocks.append("<b>📥 수입 급증(생산·투자 선행)</b>\n"
                      + " · ".join(f"{i} {_pct(v)}" for i, v in imp))

    sigs = [c for c in (cards or []) if c.get("title") and c.get("body")]
    if sigs:
        body = "\n".join(f"• <b>{c['title']}</b> — {c['body']}" for c in sigs)
        blocks.append(f"<b>🔍 데이터가 말하는 추가 신호</b>\n{body}")
    return "\n\n".join(blocks)


def record_snapshot(conn, cards, *, cost_krw=None, now=None) -> str | None:
    """그 시점 산업트렌드 전체를 동결 페이지로 저장 + 색인 1건 기록.
    반환=확정월 키(데이터 없으면 None)."""
    by_ind = industry.load_stored(conn)
    if not by_ind:
        return None
    by_imp = industry.load_stored_imports(conn)
    by_mti = industry.load_mti_stored(conn)
    by_mti_imp = industry.load_mti_imports(conn)

    series = industry.industry_series(by_ind)
    latest = ""
    for pts in series.values():
        if pts and pts[-1]["ym"] > latest:
            latest = pts[-1]["ym"]
    if not latest:
        return None

    # 1) 동결 페이지: 산업트렌드 뷰 '전체'(🔍박스 + 산업/하위품목 전부)
    inner = (llm_insights.render_html(cards or [])
             + industry.render_industry_html(by_ind, by_imp, by_mti, by_mti_imp))
    page = render_standalone(inner, latest=latest, nav_links=_ARCHIVE_NAV)
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{latest}.html"
    (SNAP_DIR / fname).write_text(page, encoding="utf-8")

    # 2) 색인 레코드: 본문 = 그 달 전체 텍스트(검색 인덱스) + 동결 페이지 링크
    now = now or datetime.now(_KST)
    summary = _summary_text(by_ind, by_imp, cards)
    link = (
        f'<a href="archive/{fname}" style="display:inline-block;margin-top:8px;'
        'padding:8px 14px;background:var(--accent);color:#fff;border-radius:8px;'
        'text-decoration:none;font-weight:600">'
        '🔗 이 시점 산업트렌드 전체 화면 보기 →</a>'
    )
    body = (summary + "\n\n" + link) if summary else link
    rec = {
        "key": latest,
        "_date": now.date().isoformat(),
        "ts": now.isoformat(timespec="seconds"),
        "title": f"{latest} 확정 산업트렌드",
        "kind": "📊 월간 스냅샷",
        "body": body,
        "file": f"archive/{fname}",
    }
    if cost_krw:
        rec["cost_krw"] = cost_krw
    _upsert(rec)
    return latest


def regenerate(out_path: Path | None = None) -> Path:
    """jsonl → 날짜 그룹 색인 HTML. 빈 상태도 안전."""
    runs = load_runs()
    months = len({(r.get("key") or "")[:7] for r in runs if r.get("key")})
    html = render_archive_page(
        runs=runs,
        title="📈 산업트렌드 월별 아카이브",
        subtitle="각 월 카드 → '전체 화면 보기'로 그 시점 산업트렌드 전체를 그대로 "
                 "다시 봅니다 · 관세청 확정치 기준 · 월/일 접기·검색·테마 토글",
        field_map=_FM,
        nav_html='<a href="index.html">← 대시보드</a>',
        stats=[Stat(value=str(len(runs)), label="동결 스냅샷"),
               Stat(value=str(months), label="확정월")],
        empty_message="아직 동결된 스냅샷이 없습니다. 다음 확정월(매월 ~15일) "
                      "유입 시 그 시점 산업트렌드 전체가 자동 동결됩니다.",
    )
    out = out_path or ARCHIVE_HTML
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def write_export(conn, cards) -> str | None:
    """공개 스냅샷을 확정월별 파일(SHARE_DIR/<YYYY-MM>.html)로 기록 →
    /share/<token>/<ym>.html 로 서빙. 새 확정월이 오면 새 파일이 생기고
    지난달 파일은 그대로 동결돼, 이미 보낸 링크는 그 시점 스냅샷을 유지한다
    (운영자 라이브 대쉬보드만 계속 갱신). 반환=ym(없으면 None). best-effort."""
    try:
        ym = latest_stored_ym(conn)
        if not ym:
            return None
        html = render_export(conn, cards)
        if not html:
            return None
        SHARE_DIR.mkdir(parents=True, exist_ok=True)
        (SHARE_DIR / f"{ym}.html").write_text(html, encoding="utf-8")
        return ym
    except Exception:
        return None


def ensure_exists() -> None:
    """대시보드 링크가 404 안 나도록 색인 HTML 없으면 생성(빈 상태 OK).
    best-effort — 실패해도 메인 렌더를 막지 않는다."""
    try:
        if not ARCHIVE_HTML.exists():
            regenerate()
    except Exception:
        pass
