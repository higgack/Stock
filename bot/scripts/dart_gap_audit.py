"""🧾 DART 못 물어본 곳 · 키 없음 사유 — 운영 실데이터로 매일 잰다(실수 #430).

#429 가 만든 두 갈래는 **드물게만** 탄다 — ① 목록·원문 조회가 일시 실패한 날,
② DART 키가 없는 날. 단위 회귀는 가짜로 그 경로를 태우지만, 운영(키 있음 · 조회
대개 성공)에서는 그 화면을 볼 일이 없어 '실제 데이터에서도 그렇게 되나' 를 아무도
재지 못했다. 이 감사가 매일 잰다:

  ① 운영 캐시 실측(읽기만 · 네트워크 0) — 표 롤링·수주잔고가 실제로 구운 기록을
     읽는다.
     · 24시간 기록에 '최신이 아닐 수 있습니다' 표가 섞였나(못 물어본 결과가 길게
       구워졌다) — 표 롤링만(수주잔고 기록엔 그 칸이 없다)
     · 짧은 기록에 시각·갈래가 빠졌나
     · 읽는 쪽(`_tables_cached`·`_bl_cached`)이 수명 규칙대로 읽나
     · 실제로 일어난 짧은 기록(못 물어본 곳)을 표본과 함께 적는다
  ② 키 없는 실수집·실렌더(격리 자식 프로세스) — 운영 종목 하나를 **API 키를
     하나도 안 넘긴** 자식에서 실제 진입점으로 태운다: `collect_stock_snapshot` ·
     `render_lookup_detail`(종목 페이지) · `build_live_quote(full)`(라이브 오버레이) ·
     분기실적 탭 · 성장 카드 · 차트 공시 마커 · DART 피드 · trade 회사 보고서.
     · 네 칸(`KEYLESS_SECTIONS`)이 '키 없음' 기록을 남기나
     · 기록된 칸마다 종목 페이지에 사유 문장이 실리나
     · 라이브 오버레이가 그 칸을 갈아끼우며 사유를 지우지 않나(두 경로가 갈리지 않나)
     · 다른 화면들이 사유를 말하나
     · 대조군: 키 있는 운영 렌더(quote_cache FULL · DART 축 1)에 그 문장이 없나 · 운영
       대시보드가 최근 키 없이 그린 기록(DART 축 0)이 있나

자식의 격리: HOME·cwd 를 임시 디렉터리로(운영 캐시·쿨다운·아카이브를 안 읽고 안
쓴다 — #264·#283 진단이 운영 상태를 바꾸면 안 된다) · 환경은 **허용 목록**만
넘긴다(API 키가 하나도 없다 — 거부 목록은 다음 키 이름을 못 막는다, #24) ·
`PYTHONPATH` 는 부모 것을 잇는다(테스트의 자식 그물이 그 경로로 걸린다, #401).
그래서 자식이 보는 것은 '캐시 없는 키 부재' 다 — 운영에서 키만 빠진 상태보다
빈칸이 더 많은 쪽이라, 사유 문장이 실려야 할 자리를 가장 많이 재는 상태다.

⚠️ 못 보는 축(#274): ① 은 **이미 구워진 기록**만 본다 — 못 물어본 결과를 24시간
기록으로 굽고 화면엔 `stale_note` 를 안 단 경우(두 결함이 겹친 경우)는 보이지
않는다. ② 는 한 종목·한 시점이다 — 그 종목에 없는 칸(예: 공시가 원래 없는 회사)은
판정 불가로 남긴다. 다른 시장(비-KR)은 DART 를 안 쓰므로 재지 않는다.

  cd ~/stock && .venv/bin/python -m bot.scripts.dart_gap_audit           # ①·②
  cd ~/stock && .venv/bin/python -m bot.scripts.dart_gap_audit --cache   # ① 만(네트워크 0)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_VER = 1
_CHILD_FLAG = "--child-keyless"
_CHILD_ENV = "NOAH_DART_GAP_CHILD"
_CHILD_TIMEOUT = 300
_DEFAULT_TICKER = "005930.KS"
# 자식에게 넘기는 환경 — **허용 목록**(#24). API 키는 이름이 무엇이든 안 넘어간다.
# 프록시·인증서 경로도 넘긴다 — 빼면 프록시·사설 CA 를 쓰는 호스트에서 자식이 아무것도
# 못 받아 '시세 원천 응답 없음' 으로 원천을 탓한다(격리 탓인데, 실수 #430 독립 리뷰).
# 값은 주소·파일 경로라 키가 아니다(이름에 KEY·TOKEN 이 든 것은 여전히 안 넘어간다).
_CHILD_ENV_KEEP = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
                   "SYSTEMROOT", "PYTHONIOENCODING",
                   "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
                   "https_proxy", "http_proxy", "no_proxy",
                   "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
# 수명 경계 ±초는 판정하지 않는다 — 재는 사이에도 시간이 흐른다.
_EDGE_SEC = 5.0
# 운영 대시보드가 키 없이 그린 기록(DART 축 0)을 '최근' 으로 볼 창(초).
_K0_RECENT_SEC = 24 * 3600


# ── ① 운영 캐시 실측 ──────────────────────────────────────────────────────
def _read(path: Path):
    """기록 하나 → dict 또는 None(못 읽음). 쓰는 중인 파일일 수 있어 한 번 더 본다."""
    for i in range(2):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if i == 0:
                time.sleep(0.2)
    return None


def _scan(cache_dir: Path, prefix: str, sig: str) -> dict:
    """`prefix*.json` → 현 지문 기록 · 옛 지문 수 · 깨진 이름."""
    out = {"cur": [], "old": 0, "broken": []}
    if not cache_dir.is_dir():
        return out
    for f in sorted(cache_dir.glob(f"{prefix}*.json")):
        if not f.name.startswith(f"{prefix}{sig}_"):
            out["old"] += 1
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        rec = _read(f)
        if not isinstance(rec, dict):
            out["broken"].append(f.name)
            continue
        out["cur"].append((f.name, mtime, rec))
    return out


def _judge_records(kind: str, scan: dict, reader, *, ttl: float, prov: float,
                   now: float) -> list[str]:
    """한 캐시의 현 지문 기록을 판정 → 출력 줄들.

    `kind` 는 `"tables"`(짧은 갈래가 'empty'·'partial' 문자열 · 표마다 `stale_note`)
    또는 `"backlog"`(짧은 기록이 `short=True`). `reader(name)` 은 제품의 읽는 쪽 —
    그 결과를 수명 규칙으로 계산한 기대와 대조한다(#35 화면이 쓰는 그 경로)."""
    label = {"tables": "표 롤링", "backlog": "수주잔고"}[kind]
    lines: list[str] = []
    cur = scan["cur"]
    long_bad, shape_bad, reader_bad, alive = [], [], [], []
    n_full = n_short = n_judged = n_edge = n_expired = 0
    for name, mtime, rec in cur:
        short = rec.get("short")
        if kind == "tables":
            ok_shape = "data" in rec and short in (None, "empty", "partial")
        else:
            ok_shape = "why" in rec and short in (None, False, True)
        at = rec.get("at")
        if short and not isinstance(at, (int, float)):
            ok_shape = False
        if not ok_shape:
            shape_bad.append(name)
            continue
        age = now - mtime
        if short:
            n_short += 1
        else:
            n_full += 1
            if kind == "tables":
                data = rec.get("data") or {}
                if any(isinstance(t, dict) and t.get("stale_note")
                       for t in data.values()):
                    long_bad.append(name)
        # 수명 규칙 — 읽는 쪽은 mtime 이 ttl 안이고, 짧은 기록은 기록 안의 시각도
        # prov 안일 때만 읽는다.
        edges = [abs(age - ttl)]
        expect = age < ttl
        if short:
            edges.append(abs((now - at) - prov))
            expect = expect and (now - at) < prov
            if expect:
                alive.append((name, rec))
            else:
                n_expired += 1
        if min(edges) < _EDGE_SEC:
            n_edge += 1
            continue
        n_judged += 1
        got = reader(name) is not None
        if got != expect:
            reader_bad.append(f"{name}({'읽음' if got else '안 읽음'}·"
                              f"기대 {'읽음' if expect else '안 읽음'})")
    lines.append(
        f"  {label}: 현 지문 기록 {len(cur)} — 24시간 {n_full} · 짧은 {n_short}"
        f"(살아 있음 {len(alive)} · 만료 {n_expired}) · 옛 지문 {scan['old']}"
        f"(재시작 때 정리) · 깨짐 {len(scan['broken'])}")
    if scan["broken"]:
        lines.append(f"  ⚠️ {label} — 못 읽는 기록 {len(scan['broken'])}건(읽는 쪽은 "
                     f"빈손으로 본다 · 다음 쓰기가 덮는다): "
                     f"{', '.join(scan['broken'][:3])}")
    if not cur:
        lines.append(f"  ❓ {label} — 읽을 수 있는 현 지문 기록이 없다(배포 직후이거나 "
                     "아무도 그 탭을 안 열었다) — 오늘은 판정할 것이 없다")
        return lines
    if long_bad:
        lines.append(f"  ❌ {label} — 못 물어본 결과가 24시간 기록으로 구워졌다"
                     f"(표에 '최신이 아닐 수 있습니다' · 짧은 기록이어야 함) "
                     f"{len(long_bad)}건: {', '.join(long_bad[:3])}")
    if shape_bad:
        lines.append(f"  ❌ {label} — 기록 형식 이상(짧은 기록의 시각·갈래 누락 등) "
                     f"{len(shape_bad)}건: {', '.join(shape_bad[:3])}")
    if reader_bad:
        lines.append(f"  ❌ {label} — 읽는 쪽이 수명 규칙과 다르게 읽는다 "
                     f"{len(reader_bad)}건: {', '.join(reader_bad[:3])}")
    if not (long_bad or shape_bad or reader_bad):
        lines.append(f"  ✅ {label} — 24시간 기록 {n_full}건에 못 물어본 결과 없음 · "
                     f"형식 이상 0 · 읽는 쪽 수명 일치 {n_judged}건"
                     f"(경계 {n_edge}건 제외)")
    for name, rec in alive[:3]:
        note = ""
        if kind == "tables":
            note = next((t.get("stale_note") for t in (rec.get("data") or {}).values()
                         if isinstance(t, dict) and t.get("stale_note")), "")
            note = note or f"갈래 {rec.get('short')}"
        else:
            note = f"사유 {rec.get('why')}"
        lines.append(f"  · 실제로 못 물어본 곳(살아 있는 짧은 기록): {name} — "
                     f"{str(note)[:90]}")
    return lines


def cache_section(*, now: float | None = None) -> list[str]:
    """① — 운영 캐시를 읽기만 한다(네트워크 0 · 쓰기 0)."""
    now = time.time() if now is None else now
    import bot.dart_backlog as bl
    import bot.dart_production as dp
    from bot.dart_client import PROVISIONAL_TTL_SEC
    from bot.finviz_client import _CACHE_DIR
    lines = ["① 못 물어본 곳은 짧게만 굽나 — 운영 캐시 실측(읽기만)"]
    lines += _judge_records(
        "tables", _scan(_CACHE_DIR, dp._TABLES_PREFIX, dp._parse_sig()),
        dp._tables_cached, ttl=dp._TABLES_TTL, prov=PROVISIONAL_TTL_SEC, now=now)
    lines += _judge_records(
        "backlog", _scan(_CACHE_DIR, bl._BL_PREFIX, bl._parse_sig()),
        bl._bl_cached, ttl=bl._BL_TTL, prov=PROVISIONAL_TTL_SEC, now=now)
    return lines


# ── ② 키 없는 실수집·실렌더 ───────────────────────────────────────────────
def _quote_dir() -> Path:
    from bot.dashboard_server import _ARCHIVE_ROOT
    return _ARCHIVE_ROOT.parent / "quote_cache"


def _ticker_of(safe: str) -> str:
    """`005930_KS` → `005930.KS` (`_quote_parse` 가 준 safe 조각)."""
    head, _, mkt = safe.rpartition("_")
    return f"{head}.{mkt}"


def _quote_files(qdir: Path, kind: str, dart: str):
    """시세 캐시 중 지금 규약의 이름이고 그 종류·DART 축 값인 파일 → (파일, safe).
    이름은 `dashboard_server._quote_parse` 로 읽는다 — 꼬리에 다른 키 축(KRX·
    DATA_GO_KR·Finnhub)이 붙어도 DART 축만 본다(#38 · 실수 #430)."""
    from bot.dashboard_server import _quote_parse, _quote_tag_axis
    if not qdir.is_dir():
        return
    for f in qdir.glob("*.json"):
        got = _quote_parse(f.name)
        if (got and got[1] == kind
                and _quote_tag_axis(got[2], "k") == dart):
            yield f, got[0]


def pick_control(qdir: Path) -> tuple[str, Path | None]:
    """키 있는 운영 렌더(FULL · 현 렌더러 · DART 축 1)가 있는 가장 최근 국내 종목 →
    (티커, 그 파일). 없으면 (기본 종목, None)."""
    best = None
    if qdir.is_dir():
        for f, safe in _quote_files(qdir, "full", "1"):
            t = _ticker_of(safe)
            if not t.upper().endswith((".KS", ".KQ")):
                continue
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if best is None or m > best[0]:
                best = (m, t, f)
    return (best[1], best[2]) if best else (_DEFAULT_TICKER, None)


def recent_keyless_renders(qdir: Path, *, now: float | None = None) -> list[tuple[str, float]]:
    """운영 대시보드가 **키 없이** 그린 시세 본문(현 렌더러 · DART 축 0) 중 최근 것 →
    [(이름, 나이초)]. 대시보드만 이 캐시를 쓰므로 DART 축 0 은 그 프로세스에 키가
    없었다는 운영 사실이다(실수 #429 델타 L1 이 이름을 키 유무로 갈랐다)."""
    now = time.time() if now is None else now
    out = []
    if not qdir.is_dir():
        return out
    for kind in ("full", "light"):
        for f, _safe in _quote_files(qdir, kind, "0"):
            try:
                age = now - f.stat().st_mtime
            except OSError:
                continue
            if age < _K0_RECENT_SEC:
                out.append((f.name, age))
    return sorted(out, key=lambda x: x[1])


def child_env(parent: dict, home: str, repo: str) -> dict:
    """자식 환경 — 허용 목록 + 임시 HOME + 레포를 앞에 둔 PYTHONPATH."""
    env = {k: parent[k] for k in _CHILD_ENV_KEEP if k in parent}
    pp = parent.get("PYTHONPATH") or ""
    env.update(HOME=home, PYTHONPATH=repo + (os.pathsep + pp if pp else ""),
               PYTHONDONTWRITEBYTECODE="1")
    env[_CHILD_ENV] = "1"
    return env


def run_child(ticker: str, *, timeout: float = _CHILD_TIMEOUT) -> tuple[dict | None, str]:
    """격리 자식을 띄워 `child_run` 결과(JSON 한 줄)를 받는다 → (결과, 오류)."""
    repo = str(Path(__file__).resolve().parents[2])
    home = tempfile.mkdtemp(prefix="noah-dartgap-")
    try:
        p = subprocess.run(
            [sys.executable, "-m", "bot.scripts.dart_gap_audit", _CHILD_FLAG, ticker],
            cwd=home, env=child_env(dict(os.environ), home, repo),
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    finally:
        shutil.rmtree(home, ignore_errors=True)
    got = [ln for ln in (p.stdout or "").splitlines() if ln.startswith("{")]
    if p.returncode != 0 or not got:
        tail = [ln for ln in (p.stderr or "").splitlines() if ln.strip()][-1:]
        return None, f"exit={p.returncode} {tail[0][:200] if tail else ''}".strip()
    try:
        return json.loads(got[-1]), ""
    except ValueError as exc:
        return None, f"결과를 못 읽음({exc})"


def _pane_of(html: str, pos: int) -> str:
    """`html` 의 위치 `pos` 가 속한 pane id(`si-…`) — 없으면 `""`.
    pane 은 `class="si-pane…" id="si-…"` 로 열린다(`si-pane active` 도 있다)."""
    import re
    last = ""
    for m in re.finditer(r'class="si-pane[^"]*" id="([^"]+)"', html[:pos]):
        last = m.group(1)
    return last


def _panes_with(html: str, section: str) -> list[str]:
    """`html` 에서 그 칸의 키 없음 문장이 실린 pane id 들(문장 틀은 단일 출처)."""
    from bot.dart_client import KEYLESS_WHAT, keyless_sentence_spans
    out: list[str] = []
    for pos, what in keyless_sentence_spans(html or ""):
        if KEYLESS_WHAT[section] in what:
            pid = _pane_of(html, pos)
            if pid not in out:
                out.append(pid)
    return out


def _sentence(text) -> str:
    """첫 키 없음 문장의 대상(없으면 `""`) — 화면 표면 판정용."""
    from bot.dart_client import keyless_sentences
    got = keyless_sentences(text if isinstance(text, str) else "")
    return got[0] if got else ""


def child_run(ticker: str) -> dict:
    """자식 안에서만 — 키가 없다는 것부터 재고, 실제 진입점으로 수집·렌더한다."""
    out: dict = {"v": _VER, "ticker": ticker}
    from bot.dart_client import (KEYLESS_SECTIONS, dart_ready, get_dart,
                                 keyless_sentence_in, keyless_when)
    out["key_absent"] = not dart_ready(get_dart())
    if not out["key_absent"]:
        return out
    # LLM 비용 0 은 환경 허용 목록이 보장한다(자식엔 어떤 API 키도 없다 — `child_env`).
    from bot.stock_snapshot import collect_stock_snapshot
    snap = collect_stock_snapshot(ticker, use_cache=False)
    out["snapshot"] = bool(snap)
    if snap:
        # 스냅샷이 있어야 재는 칸들 — 시세 원천이 막히면 판정 불가로 남기고 아래
        # 화면들은 그대로 잰다(그 화면들은 스냅샷이 필요 없다).
        kr = snap.get("kr") or {}
        out["flags"] = {s: keyless_when(kr, s) for s in KEYLESS_SECTIONS}
        from bot.dashboard import build_live_quote, render_lookup_detail
        page = render_lookup_detail(ticker, enrich=True) or ""
        out["page"] = {s: _panes_with(page, s) for s in KEYLESS_SECTIONS}
        live = build_live_quote(ticker, full=True) or {}
        lp = live.get("panes") or {}
        out["live_panes"] = sorted(lp)
        out["live"] = {s: sorted(pid for pid, h in lp.items()
                                 if keyless_sentence_in(h or "", s))
                       for s in KEYLESS_SECTIONS}
    surf: dict = {}
    try:
        from bot.quarterly_infographic import get_or_render
        surf["분기실적 탭"] = _sentence((get_or_render(ticker) or {}).get("error"))
    except Exception as exc:                                   # noqa: BLE001
        surf["분기실적 탭"] = f"!{type(exc).__name__}: {exc}"[:160]
    try:
        import datetime as _dt

        from bot.dart_growth_risk import build_growth_risk
        r = build_growth_risk(get_dart(), ticker, _dt.date.today().year - 1,
                              "11011", {})
        surf["성장 카드"] = _sentence((r or {}).get("error"))
    except Exception as exc:                                   # noqa: BLE001
        surf["성장 카드"] = f"!{type(exc).__name__}: {exc}"[:160]
    try:
        from bot.chart_data import fetch_chart_payload
        cp = fetch_chart_payload(ticker, "1d", "1y")
        if not cp:
            surf["차트 공시 마커"] = None          # 시세를 못 받았다 — 판정 불가
        elif cp.get("events"):
            surf["차트 공시 마커"] = None          # 마커가 다른 데서 왔다 — 판정 불가
        else:
            surf["차트 공시 마커"] = _sentence(cp.get("events_note"))
    except Exception as exc:                                   # noqa: BLE001
        surf["차트 공시 마커"] = f"!{type(exc).__name__}: {exc}"[:160]
    try:
        from bot.dashboard import _render_dart_feed_page
        surf["DART 피드"] = _sentence(_render_dart_feed_page({})[0])
    except Exception as exc:                                   # noqa: BLE001
        surf["DART 피드"] = f"!{type(exc).__name__}: {exc}"[:160]
    try:
        from trade.company_report import gather
        code = ticker.split(".")[0]
        surf["trade 매출표(코드)"] = _sentence(gather(code).get("products_why"))
        name = ((snap or {}).get("long_name") or (snap or {}).get("short_name")
                or "삼성전자")
        g = gather(str(name))
        surf["trade 매출표(이름)"] = (
            _sentence(g.get("products_why")) if g.get("mode") == "company"
            else None)                        # 품목으로 풀렸다 — 판정 불가
    except Exception as exc:                                   # noqa: BLE001
        surf["trade 매출표"] = f"!{type(exc).__name__}: {exc}"[:160]
    out["surfaces"] = surf
    return out


def judge_child(res: dict | None, err: str) -> list[str]:
    """자식 결과 → 판정 줄. 한 줄이 혼자 행동 가능하게 대상·갈래·근거를 싣는다(#356)."""
    from bot.dart_client import KEYLESS_SECTIONS
    if res is None:
        if err == "timeout":
            return [f"  ❓ 키 없는 실수집이 {_CHILD_TIMEOUT}초 안에 안 끝났다 — "
                    "오늘은 판정 불가(원천이 느리다)"]
        return [f"  ❌ 격리 자식이 실패했다 — 키 없는 수집·렌더 경로가 예외로 "
                f"끝났을 수 있다: {err}"]
    t = res.get("ticker", "?")
    if not res.get("key_absent"):
        return [f"  ❌ 격리 실패 — 자식이 DART 키를 찾았다({t}) · 키 없는 경로를 못 "
                "잰다(자식 환경·.env 탐색 경로 확인)"]
    lines: list[str] = []
    snap_ok = bool(res.get("snapshot"))
    if not snap_ok:
        lines.append(f"  ❓ {t} 수집 실패(시세 원천 응답 없음) — 종목 페이지·라이브 "
                     "칸은 오늘 판정 불가(아래 다른 화면은 그대로 잰다)")
    flags = res.get("flags") or {}
    page = res.get("page") or {}
    live = res.get("live") or {}
    live_panes = set(res.get("live_panes") or [])
    no_flag = [s for s in KEYLESS_SECTIONS if snap_ok and not flags.get(s)]
    if no_flag:
        lines.append(f"  ❌ {t} — 키 없이 모았는데 '키 없음' 기록이 빠진 칸: "
                     f"{', '.join(no_flag)} (화면이 그 칸의 사유를 못 말한다)")
    no_page = [s for s in KEYLESS_SECTIONS if flags.get(s) and not page.get(s)]
    if no_page:
        lines.append(f"  ❌ {t} — 기록은 있는데 종목 페이지에 사유 문장이 없는 칸: "
                     f"{', '.join(no_page)}")
    erased = [f"{s}({pid})" for s in KEYLESS_SECTIONS
              for pid in (page.get(s) or []) if pid in live_panes and not live.get(s)]
    if erased:
        lines.append(f"  ❌ {t} — 라이브 오버레이(/api/quote?full=1)가 pane 을 갈아끼우며 "
                     f"사유를 지운다: {', '.join(erased)}")
    surf = res.get("surfaces") or {}
    bad, crashed, unjudged, good = [], [], [], []
    for name, got in surf.items():
        if got is None:
            unjudged.append(name)
        elif isinstance(got, str) and got.startswith("!"):
            crashed.append(f"{name} {got[1:]}")
        elif got:
            good.append(name)
        else:
            bad.append(name)
    if bad:
        lines.append(f"  ❌ {t} — 키가 없는데 사유를 말하지 않는 화면: {', '.join(bad)}")
    if crashed:
        lines.append(f"  ❌ {t} — 키 없이 부르면 예외로 끝나는 화면: "
                     f"{'; '.join(crashed)[:240]}")
    if unjudged:
        lines.append(f"  ❓ {t} — 판정 불가(시세 없음·다른 원천의 값·품목으로 풀림): "
                     f"{', '.join(unjudged)}")
    if not (no_flag or no_page or erased or bad or crashed):
        n_page = sum(1 for s in KEYLESS_SECTIONS if page.get(s))
        head = (f"칸 기록 {len(KEYLESS_SECTIONS)}/{len(KEYLESS_SECTIONS)} · 종목 페이지 "
                f"사유 {n_page}/{len(KEYLESS_SECTIONS)} · 라이브 일치 · "
                if snap_ok else "")
        lines.append(f"  ✅ {t} 키 없는 실수집·실렌더 — {head}"
                     f"다른 화면 {len(good)}/{len(good) + len(unjudged)}")
    return lines


def judge_control(ticker: str, control: Path | None, k0: list) -> list[str]:
    """대조군 — 키 있는 운영 렌더에는 키 없음 문장이 없어야 한다(#25 반대 증거) ·
    운영 대시보드가 최근 키 없이 그렸으면 그것 자체가 운영 사실이다."""
    from bot.dart_client import keyless_sentences
    lines: list[str] = []
    if control is None:
        lines.append(f"  ❓ 대조군 없음 — 키 있는 운영 렌더(quote_cache FULL · DART 축 1)가 "
                     f"아직 없다({ticker})")
    else:
        try:
            body = json.loads(control.read_text(encoding="utf-8"))
            panes = ((body or {}).get("quote") or {}).get("panes") or {}
        except (OSError, ValueError) as exc:
            lines.append(f"  ❓ 대조군을 못 읽음({control.name}: {exc})")
            panes = None
        if panes is not None:
            hits = [f"{pid}: {got}" for pid, h in panes.items()
                    for got in keyless_sentences(h or "")]
            if hits:
                lines.append(f"  ⚠️ {ticker} 키 있는 운영 렌더에 '키 없음' 문장 — 그 "
                             f"스냅샷을 모을 때 키가 없었다(다시 모으면 사라진다): "
                             f"{'; '.join(hits)[:200]}")
            else:
                lines.append(f"  ✅ 대조군 {ticker} 키 있는 운영 렌더 — '키 없음' "
                             f"문장 없음(pane {len(panes)}개)")
    if k0:
        name, age = k0[0]
        lines.append(f"  ❌ 운영 대시보드가 최근 24시간 안에 DART 키 없이 그렸다 — "
                     f"키 없이 그린 본문 {len(k0)}건(가장 최근 {name} · {age / 3600:.1f}시간 전) · "
                     "대시보드 프로세스의 키(.env·EnvironmentFile) 확인")
    return lines


def keyless_section(ticker: str | None = None) -> list[str]:
    """② — 격리 자식으로 키 없는 실수집·실렌더를 태운다."""
    lines = ["② 키 없을 때 화면이 사유를 말하나 — 격리 자식 실수집·실렌더"]
    qdir = _quote_dir()
    picked, control = pick_control(qdir)
    t = ticker or picked
    if ticker and ticker != picked:
        control = None
    res, err = run_child(t)
    lines += judge_child(res, err)
    lines += judge_control(t, control, recent_keyless_renders(qdir))
    return lines


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == [_CHILD_FLAG]:
        if os.environ.get(_CHILD_ENV) != "1" or len(argv) < 2:
            print("이 모드는 감사가 격리 자식으로만 부른다", file=sys.stderr)
            return 2
        print(json.dumps(child_run(argv[1]), ensure_ascii=False, default=str))
        return 0
    print(f"[dart_gap_audit v{_VER}] 인터프리터 {sys.executable}")
    lines: list[str] = []
    for title, fn in (("①", cache_section),
                      ("②", None if "--cache" in argv else keyless_section)):
        if fn is None:
            lines.append(f"{title} 건너뜀(--cache)")
            continue
        try:
            if fn is keyless_section and "--ticker" in argv:
                i = argv.index("--ticker")
                lines += fn(argv[i + 1] if i + 1 < len(argv) else None)
            else:
                lines += fn()
        except Exception as exc:                               # noqa: BLE001
            lines.append(f"{title} 감사 섹션이 예외로 끝났다")
            lines.append(f"  ❌ {title} 섹션 실패: {type(exc).__name__}: {exc}")
    print("\n".join(lines))
    return 1 if any("❌" in ln for ln in lines) else 0


if __name__ == "__main__":
    sys.exit(main())
