"""수출입(trade) 대시보드 전수 감사 — 화면 vs store.db 대조 + 생성물 무결성.

사용자 2026-08-20: "Trade 도 감사만들어줘" — NOAH 쪽 감사(bot/audit_sweep)와
같은 원칙이되, trade 는 **별도 체크아웃(~/stock-trade)·별도 데이터(~/.trade)**
에서 돌므로 여기(trade/scripts) 전용 진입점 + 전용 타이머로 돈다.

원칙(루트 CLAUDE.md 실수기록 준용):
  · 화면이 쓰는 **그 로더**(store.stats / list_all_alerts dedup / 아카이브
    load_runs)로 재계산해 화면 숫자와 대조한다(#35).
  · 대조 대상 파싱이 0건이면 '이상 없음'이 아니라 **감사 실패(❌)**(#47).
  · 여유(grace)로 사실을 덮지 않는다 — 뒤처짐은 항상 말한다(#41).

보는 것:
  ① index.html 헤더 vs store.db — 총/최신/수출/수입/잠정/확정/품목 7개 숫자
  ② 갱신 신선도 — 5분 재렌더 주기(trade-bot-dashboard-refresh.timer) 대비
  ③ 참조 파일 무결성 — lazy 패널·nav 형제 페이지가 실재하고 비어있지 않은가
     (regen 실패는 WARNING 로그뿐이라 끝상태는 아무도 안 본다 — 여기서 본다)
  ④ 아카이브 카운트 — report/industry 아카이브 '총 N건' vs jsonl 로더
  ⑤ 미파싱 백로그 — eval_misses.jsonl 이 비어있지 않으면 헤더에 백로그 줄

실행(수동 · VM trade 체크아웃):
    cd ~/stock-trade && .venv/bin/python -m trade.scripts.dashboard_audit
자동: trade-bot-dashboard-audit.timer (매일 08:10 KST) — ❌ 있을 때만
텔레그램(health_check 와 같은 _notify · 무음 규율).

읽기 전용 · LLM 0 · ₩0.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROBE_VER = 1
_KST = timezone(timedelta(hours=9))
_OK, _NG, _WARN = "✅", "❌", "⚠️"

# 5분 재렌더(trade-bot-dashboard-refresh.timer) — 여유 6배.
_INDEX_MAX_AGE_MIN = 30

# nav 형제 페이지(dashboard.main 이 **매 렌더 보장** — 404 방지 정책).
# badonion 소스 페이지는 레지스트리에서 동적으로 보탠다(#24: 열거 고정 금지).
# ⚠️ lazy 패널(industry_panel/heatmap/industry_csv/alerts_history)은 여기 아님 —
# 데이터가 없으면 렌더러가 **인라인 폴백**하고 파일도 참조도 안 만든다. E2E
# 픽스처(빈 customs.db)에서 이 감사가 그걸 ❌ 로 오보했다 → index.html 이
# 실제로 참조(data-src / fetch)할 때만 요구한다(#50: 내 가정이 아니라 렌더러의
# 실제 계약을 검사).
_SIBLINGS_STATIC = (
    "jp.html", "reference.html",
    "industry_archive.html", "report_archive.html",
)

# index.html 의 lazy 참조 세 형태(원천 코드로 확인 — #50):
#   · <main … data-src="X.html">              (industry/heatmap 탭)
#   · const HISTORY_SRC="X.json"              (모달 히스토리; 빈문자 = 인라인)
#   · const INDUSTRY_CSV_SRC="X.html"         (산업 CSV; 빈문자 = 인라인)
_REF_RE = re.compile(
    r'data-src="([a-zA-Z0-9_.\-]+)"'
    r'|const (?:HISTORY_SRC|INDUSTRY_CSV_SRC)="([a-zA-Z0-9_.\-]+)"')


def _p(*a):
    print(*a, flush=True)


def _mark(ok, warn=False):
    return _WARN if warn else (_OK if ok else _NG)


def _data_dir() -> Path:
    return Path(os.environ.get("TRADE_DATA_DIR") or Path.home() / ".trade")


def parse_header(html: str) -> dict | None:
    """index.html 헤더의 7개 숫자 + 갱신시각. 파싱 실패 = None(호출부가 ❌).

    ⚠️ 갱신시각은 **UTC 표기**다(`갱신 2026-08-20 06:40 UTC` —
    dashboard.py:690 `strftime("%Y-%m-%d %H:%M UTC")`). 처음 쓴 정규식은
    숫자·기호만 받아 'UTC' 글자에서 통째로 미매칭됐다 — 픽스처를 실제 렌더
    문자열로 안 만들었으면 이 감사는 영원히 '파싱 0건 ❌'만 찍었을 것이다
    (#54: 원천 형식을 원천 코드로 확인하고 시작할 것)."""
    m = re.search(
        r"갱신 ([0-9 :.\-]+ UTC) · 총 ([\d,]+)건 \(최신 ([\d,]+)개\) · "
        r"수출 ([\d,]+) / 수입 ([\d,]+) · 잠정 ([\d,]+) / 확정 ([\d,]+) · "
        r"품목 ([\d,]+)", html)
    if not m:
        return None
    g = [x.replace(",", "") for x in m.groups()]
    return {"updated": g[0].strip(), "total": int(g[1]), "latest": int(g[2]),
            "export": int(g[3]), "import": int(g[4]), "prelim": int(g[5]),
            "final": int(g[6]), "items": int(g[7])}


def latest_count(all_alerts: list[dict]) -> int:
    """dedup_key 당 1개 — dashboard.render_html 과 **같은 규칙**(첫 등장 채택;
    list_all_alerts 의 정렬이 최신 우선을 보장). 규칙을 여기 복제하지 않고
    갯수만 세므로 정렬 세부가 바뀌어도 카운트 계약은 유지된다."""
    seen: set[str] = set()
    for a in all_alerts:
        seen.add(a.get("dedup_key") or "")
    return len(seen)


def audit_index(dash_dir: Path, db_path: Path) -> list[str]:
    bad: list[str] = []
    _p("\n" + "=" * 72)
    _p("① index.html 헤더 vs store.db")
    _p("=" * 72)
    fp = dash_dir / "index.html"
    if not fp.exists():
        bad.append("index.html 미존재")
        _p(f"{_NG} index.html 미존재 — {fp}")
        return bad
    html = fp.read_text(encoding="utf-8")
    hdr = parse_header(html)
    if hdr is None:
        bad.append("index.html 헤더 파싱 0건(감사 패턴 어긋남 — 검증 불가)")
        _p(f"{_NG} 헤더 파싱 0건 — 렌더 형식이 바뀌었으면 이 감사도 같이 고칠 것")
        return bad
    from trade.store import list_all_alerts, open_db, stats
    conn = open_db(db_path)
    try:
        s = stats(conn)
        alerts = list_all_alerts(conn)
    finally:
        conn.close()
    checks = [
        ("총", hdr["total"], s.get("total", 0)),
        ("최신", hdr["latest"], latest_count(alerts)),
        ("수출", hdr["export"], s.get("by_direction", {}).get("export", 0)),
        ("수입", hdr["import"], s.get("by_direction", {}).get("import", 0)),
        ("잠정", hdr["prelim"], s.get("by_status", {}).get("preliminary", 0)),
        ("확정", hdr["final"], s.get("by_status", {}).get("final", 0)),
        ("품목", hdr["items"], s.get("distinct_items", 0)),
    ]
    for name, shown, real in checks:
        ok = shown == real
        _p(f"{_mark(ok)} {name}: 화면 {shown:,} = store {real:,}")
        if not ok:
            bad.append(f"index 헤더 {name}: 화면 {shown:,} ≠ store {real:,}")
    # visible-count: '최신 M / M 표시 중'
    m = re.search(r'id="visible-count">([\d,]+)</span> / ([\d,]+) 표시 중', html)
    if m:
        a, b = (int(x.replace(",", "")) for x in m.groups())
        ok = a == b == hdr["latest"]
        _p(f"{_mark(ok)} 표시 카운트 {a}/{b} = 최신 {hdr['latest']}")
        if not ok:
            bad.append(f"표시 카운트 {a}/{b} ≠ 최신 {hdr['latest']}")
    else:
        bad.append("visible-count 파싱 0건")
        _p(f"{_NG} visible-count 파싱 0건")
    # ② 신선도 — 갱신시각(KST)
    _p("")
    _p("② 갱신 신선도 (5분 재렌더 주기)")
    try:
        # 헤더 시각은 UTC(위 parse_header 주석) — UTC 로 비교해야 9시간 오차가
        # 없다(실수기록 전역표기 10a: 시각 비교는 명시 타임존).
        upd = datetime.strptime(hdr["updated"][:16], "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - upd).total_seconds() / 60
        ok = age_min <= _INDEX_MAX_AGE_MIN
        _p(f"{_mark(ok)} 갱신 {hdr['updated']} — {age_min:.0f}분 전 "
           f"(상한 {_INDEX_MAX_AGE_MIN}분)")
        if not ok:
            bad.append(f"index.html 이 {age_min:.0f}분째 미갱신 — "
                       "trade-bot-dashboard-refresh.timer 확인")
    except ValueError:
        bad.append(f"갱신시각 파싱 실패: {hdr['updated']!r}")
        _p(f"{_NG} 갱신시각 파싱 실패: {hdr['updated']!r}")
    return bad


def audit_siblings(dash_dir: Path) -> list[str]:
    bad: list[str] = []
    _p("\n" + "=" * 72)
    _p("③ 참조 파일 무결성 (lazy 패널 · nav 형제 페이지)")
    _p("=" * 72)
    names = list(_SIBLINGS_STATIC)
    try:
        from trade import badonion_sources
        names += [s.html_file for s in badonion_sources.SOURCES if s.html_file]
    except Exception as exc:                                  # noqa: BLE001
        bad.append(f"badonion_sources 로드 실패: {exc}")
        _p(f"{_NG} badonion_sources 로드 실패: {exc}")
    # lazy 산출물 — index.html 이 실제로 참조하는 것만(인라인 폴백이면 참조 없음)
    idx = dash_dir / "index.html"
    if idx.exists():
        html = idx.read_text(encoding="utf-8")
        refs = sorted({a or b for a, b in _REF_RE.findall(html) if (a or b)})
        _p(f"   (index.html 참조 lazy 산출물 {len(refs)}개: {refs})")
        names += refs
    for n in names:
        fp = dash_dir / n
        if not fp.exists():
            bad.append(f"{n} 미존재(nav/lazy 404)")
            _p(f"{_NG} {n} 미존재 — nav 링크/lazy fetch 가 404")
        elif fp.stat().st_size == 0:
            bad.append(f"{n} 0바이트")
            _p(f"{_NG} {n} 0바이트")
        else:
            _p(f"{_OK} {n} ({fp.stat().st_size / 1024:.0f} KB)")
    return bad


def audit_archives(dash_dir: Path) -> list[str]:
    bad: list[str] = []
    _p("\n" + "=" * 72)
    _p("④ 아카이브 '총 N건' vs 로더")
    _p("=" * 72)
    for name, mod_name in (("report_archive.html", "trade.report_archive"),
                           ("industry_archive.html", "trade.industry_archive")):
        fp = dash_dir / name
        if not fp.exists():
            continue        # ③ 에서 이미 ❌
        html = fp.read_text(encoding="utf-8")
        m = re.search(r'id="scr-status"[^>]*>총 ([\d,]+)건', html)
        if not m:
            bad.append(f"{name} 상태줄 파싱 0건")
            _p(f"{_NG} {name} 상태줄 파싱 0건(검증 불가)")
            continue
        shown = int(m.group(1).replace(",", ""))
        mod = __import__(mod_name, fromlist=["load_runs"])
        real = len(mod.load_runs())
        ok = shown == real
        _p(f"{_mark(ok)} {name}: 화면 {shown} = 로더 {real}")
        if not ok:
            bad.append(f"{name}: 화면 {shown} ≠ 로더 {real} — regen 누락 의심")
    return bad


def audit_backlog(dash_dir: Path) -> list[str]:
    bad: list[str] = []
    _p("\n" + "=" * 72)
    _p("⑤ 미파싱 백로그(eval_misses) ↔ 헤더 표기")
    _p("=" * 72)
    misses = _data_dir() / "eval_misses.jsonl"
    n = 0
    if misses.exists():
        n = sum(1 for ln in misses.read_text(encoding="utf-8").splitlines()
                if ln.strip())
    fp = dash_dir / "index.html"
    html = fp.read_text(encoding="utf-8") if fp.exists() else ""
    has_line = "미파싱 백로그" in html
    if n and not has_line:
        bad.append(f"미파싱 백로그 {n}건인데 헤더에 표기 없음")
        _p(f"{_NG} 백로그 {n}건인데 헤더 표기 없음(#43 — 화면이 침묵)")
    else:
        _p(f"{_mark(True)} 백로그 {n}건 · 헤더 표기 {'있음' if has_line else '없음(0건이라 정상)'}")
    return bad


def run_audit() -> list[str]:
    data = _data_dir()
    dash_dir = data / "dashboard"
    db_path = data / "store.db"
    _p(f"=== trade dashboard_audit v{_PROBE_VER} · "
       f"{datetime.now(_KST).strftime('%Y-%m-%d %H:%M:%S')} KST · "
       f"data={data} ===")
    bad: list[str] = []
    for fn, args in ((audit_index, (dash_dir, db_path)),
                     (audit_siblings, (dash_dir,)),
                     (audit_archives, (dash_dir,)),
                     (audit_backlog, (dash_dir,))):
        try:
            bad += fn(*args)
        except Exception as exc:                              # noqa: BLE001
            import traceback
            traceback.print_exc()
            bad.append(f"{fn.__name__} 예외: {type(exc).__name__}: {exc}")
    return bad


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    bad = run_audit()
    _p("\n" + "=" * 72)
    if not bad:
        _p("✅ ❌ 0건 — 알림 없음(무음)")
        return 0
    _p(f"❌ {len(bad)}건:")
    for b in bad:
        _p(f"  • {b}")
    if "--notify" in argv:
        # health_check 와 같은 전송 경로(설정 없으면 조용히 skip — dev 안전).
        from trade.scripts.health_check import _notify
        lines = [f"🔍 <b>수출입 대시보드 감사</b> · ❌ {len(bad)}건"]
        lines += [f"• {b}" for b in bad[:15]]
        if len(bad) > 15:
            lines.append(f"… 외 {len(bad) - 15}건 (VM: python -m "
                         "trade.scripts.dashboard_audit)")
        _notify("\n".join(lines))
    return 1


if __name__ == "__main__":
    sys.exit(main())
