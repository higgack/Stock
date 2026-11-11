"""전 대시보드 화면 경고 감사 — 화면이 스스로 적은 `저장분`·`⚠️` 를 읽는다.

  cd ~/stock && .venv/bin/python -m bot.scripts.page_health_audit

무엇을 보나(#24 목록이 아니라 구조로 — 새 화면은 등록 없이 덮인다):
  ① 아카이브 루트의 생성 페이지 전부(`*.html`, 조각·2주 넘은 화석 제외)
  ② 그 페이지들이 링크한 **서버 렌더 화면**(테마·업종·급등락·수출입 …) — 떠 있는
     대시보드 서버에 물어 사용자가 보는 그대로 읽는다(#35).

판정은 `bot.page_health` — 오래된 저장분은 그날, 값이 빈 화면은 이튿날, 그 밖의
경고는 나흘째에 ❌ 로 올라 일일 결산이 텔레그램으로 보낸다. 그 뒤엔 7일마다.

⚠️ 판정 글자는 판정 줄에만 쓴다 — 요약은 세기만 한다(#250·#268).
⚠️ 기록(`page_health/state.json`)은 '처음 본 날' 뿐이다 — 손으로 다시 돌려도
   같은 날이면 판정이 안 바뀐다.
"""
from __future__ import annotations

import json
import posixpath
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bot import page_health as ph

_VER = 1
_KST = timezone(timedelta(hours=9))
_FOSSIL_DAYS = 14
_LIVE_CAP = 60
_LIVE_TIMEOUT = 25
_LIVE_BUDGET = 300.0
_SKIP_EXT = (".html", ".png", ".jpg", ".svg", ".css", ".js", ".json", ".csv",
             ".xml", ".ico", ".txt", ".pdf")
_SKIP_HEAD = ("api/", "lookup", "media/", "static/")
_HREF = re.compile(r'href="([^"#?]+)')


def _p(*a):
    print(*a, flush=True)


def _state_path() -> Path:
    return Path.home() / ".tradingagents" / "cache" / "page_health" / "state.json"


def _load_state() -> dict:
    try:
        v = json.loads(_state_path().read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    fp = _state_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(fp)


def _day_diff(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def disk_pages(root: Path, now: float) -> tuple:
    """(읽을 페이지 [(이름, html)], 건너뛴 화석 수)."""
    pages, fossils = [], 0
    for fp in sorted(root.glob("*.html")):
        try:
            if now - fp.stat().st_mtime > _FOSSIL_DAYS * 86400:
                fossils += 1
                continue
            html = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "<!doctype" not in html[:300].lower():
            continue                       # lazy 조각 — 페이지가 아니다(#58)
        pages.append((fp.name, html))
    return pages, fossils


def live_routes(pages) -> list:
    """생성 페이지의 링크 → 서버가 그리는 경로(순수). 파일·API·바깥 링크는 뺀다."""
    out: list = []
    for _name, html in pages:
        for href in _HREF.findall(html):
            if re.match(r"[a-z][a-z0-9+.-]*:", href, re.I) or href.startswith("//"):
                continue
            path = posixpath.normpath(href.lstrip("/"))
            if not path or path == "." or path.startswith(".."):
                continue
            if path.lower().endswith(_SKIP_EXT) or path.startswith(_SKIP_HEAD):
                continue
            if href.endswith("/"):
                path += "/"
            if path not in out:
                out.append(path)
    return out[:_LIVE_CAP]


def _server_base() -> tuple:
    """(기준 URL, 인증) — 포트는 유닛 파일에서, 자격은 .env 에서(값은 안 찍는다)."""
    from bot.env_keys import env_key
    port = 8081
    try:
        unit = (Path(__file__).resolve().parents[2] / "deploy"
                / "stock-bot-dashboard.service").read_text(encoding="utf-8")
        m = re.search(r"dashboard_server\b.*--port\s+(\d+)", unit)
        if m:
            port = int(m.group(1))
    except OSError:
        pass
    token = env_key("DASHBOARD_TOKEN")
    user, pw = env_key("DASHBOARD_USER"), env_key("DASHBOARD_PASSWORD")
    base = f"http://127.0.0.1:{port}/" + (f"{token}/" if token else "")
    return base, ((user, pw) if user and pw else None)


def _http_get(url: str, auth) -> tuple:
    """(상태코드, 본문) — 못 닿으면 (None, 사유)."""
    import requests
    try:
        r = requests.get(url, auth=auth, timeout=_LIVE_TIMEOUT)
        return r.status_code, r.text
    except Exception as exc:                                  # noqa: BLE001
        return None, type(exc).__name__


def live_pages(routes, *, get=None, base_auth=None, clock=time.monotonic) -> tuple:
    """(페이지 [(이름, html)], 화면별 문제 [(이름, finding)], 못 읽은 사유)."""
    get = get or _http_get
    base, auth = base_auth or _server_base()
    pages, probs, t0 = [], [], clock()
    for i, r in enumerate(routes):
        if clock() - t0 > _LIVE_BUDGET:
            return pages, probs, f"시간 예산({_LIVE_BUDGET:.0f}초)에서 멈춤 — {len(routes) - i}개 못 읽음"
        code, body = get(base + r, auth)
        name = "/" + r
        if code is None:
            if not pages and i == 0:
                return pages, probs, f"대시보드 서버에 못 닿음({body})"
            probs.append((name, {"kind": "blocked", "where": "", "age_h": None,
                                 "text": f"화면을 못 읽음({body})"}))
        elif code in (401, 403):
            return pages, probs, f"대시보드 서버가 인증을 거절(HTTP {code})"
        elif code >= 400:
            probs.append((name, {"kind": "blocked", "where": "", "age_h": None,
                                 "text": f"화면이 HTTP {code} 를 냅니다"}))
        else:
            pages.append((name, body))
    return pages, probs, ""


def main(argv=None) -> int:
    from bot.archive import ARCHIVE_ROOT
    today = datetime.now(_KST).date().isoformat()
    _p(f"[page_health_audit v{_VER}] {today} KST · 아카이브 {ARCHIVE_ROOT}")
    pages, fossils = disk_pages(Path(ARCHIVE_ROOT), time.time())
    routes = live_routes(pages)
    lpages, probs, lwhy = live_pages(routes) if routes else ([], [], "")
    _p(f"생성 페이지 {len(pages)}개(2주 넘은 파일 {fossils}개 제외) · "
       f"서버 렌더 화면 {len(lpages)}/{len(routes)}개 읽음")
    if not pages:
        _p("❓ 생성 페이지를 한 장도 못 읽었습니다 — 판정 불가(아카이브 경로 확인)")
    found = [(n, f) for n, h in pages + lpages for f in ph.findings(h)] + probs
    if lwhy:
        found.append(("(서버 렌더 화면)", {"kind": "warn", "where": "", "age_h": None,
                                        "text": f"⚠ {lwhy}"}))
    state = _load_state()
    keys = [ph.state_key(n, f) for n, f in found]
    n_fail = n_warn = 0
    for (name, f), k in zip(found, keys):
        first = (state.get(k) or {}).get("first") or today
        grade, why = ph.judge(f, max(0, _day_diff(first, today)))
        mark = "❌" if grade == "fail" else "⚠️"
        n_fail += grade == "fail"
        n_warn += grade != "fail"
        where = f" › {f['where']}" if f.get("where") else ""
        _p(f"{mark} {name}{where}: {why} · 「{f['text'][:140]}」")
    try:
        _save_state(ph.roll_state(state, keys, today, _day_diff))
    except OSError as exc:
        _p(f"❓ 경과 기록을 못 썼습니다({type(exc).__name__}) — 내일 '며칠째' 가 다시 1일째부터")
    _p(f"화면 문구 {len(found)}건 — 오늘 알림 {n_fail} · 기록만 {n_warn}"
       + (" · 문구 없음(전 화면 정상 표기)" if not found and pages else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
