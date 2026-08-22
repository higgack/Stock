"""Dashboard HTTP server with archive-entry deletion support.

Drop-in replacement for `python3 -m http.server` that adds a single
POST /api/delete endpoint so the dashboard can render a 🗑️ button next
to each analysis card. GET requests serve the archive directory
exactly like the stdlib server did.

Two-layer authentication (both optional via env vars, disabled when
the corresponding var is unset):
  * URL path token: env `DASHBOARD_TOKEN`. When set, every request must
    start with `/<token>/...`; anything else returns 404. Mirrors the
    Second Brain dashboard's URL-token convention so a casual scanner
    hitting the server's public IP sees nothing.
  * HTTP Basic Auth: env `DASHBOARD_USER` + `DASHBOARD_PASSWORD`. When
    both are set, the server requires a matching `Authorization: Basic`
    header on every request, returning 401 + WWW-Authenticate otherwise.

Both layers must pass when configured. Either layer alone is plenty
weak against a sustained attack; the combination keeps a curious
passerby out without depending on TLS.

Wire protocol (after auth):
  POST /api/delete  body: {"date": "YYYY-MM-DD", "ticker": "NVDA"}
                    → 200 {"ok": true,  "deleted": [".json", ".html"]}
                    → 400 {"ok": false, "error": "..."}

Side effects of a successful delete:
  * removes archive/<date>/<ticker>.json and <ticker>.html
  * triggers regenerate_index() so index.html / errors.html refresh
    immediately (no need to wait for the next analysis or bot startup)

Path traversal is blocked by validating date and ticker against strict
regexes BEFORE constructing the filesystem path. The archive directory
itself is held as an absolute resolved Path; any computed file path
must be a child of it (Path.resolve + relative_to check) or the request
is rejected.

Run with:
    python3 -m bot.dashboard_server [--port 8081] [--bind 0.0.0.0]
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import logging
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from secrets import compare_digest

from bot.archive import ARCHIVE_ROOT
from bot.dashboard import regenerate_index

log = logging.getLogger("bot.dashboard_server")

# Load .env from the repo root regardless of CWD. Path is absolute
# (parent.parent of this module) so a wrong WorkingDirectory can't
# silently break auth. We collect a status string here (no log calls
# yet — logging isn't configured at module import time) and emit it
# in main() once basicConfig has run.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOTENV_STATUS: str
try:
    from dotenv import load_dotenv
    _env_path = _REPO_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
        _DOTENV_STATUS = f"loaded {_env_path}"
    else:
        _DOTENV_STATUS = f"{_env_path} not found"
except ImportError:
    _DOTENV_STATUS = (
        "python-dotenv not installed — "
        "credentials must come from systemd EnvironmentFile="
    )

# Strict input validation. Date matches the YYYY-MM-DD form the analyzer
# writes; ticker matches the same charset bot.telegram_bot.TICKER_RE
# allows plus uppercase enforcement (archive filenames are written in
# uppercase by bot.archive.save_analysis).
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")
_ARCHIVE_ROOT = ARCHIVE_ROOT.resolve()
# 우리 대시보드 루트 페이지명 — trade 프록시 밑으로 흡수된 상대링크 탈출용.
_OUR_ROOT_PAGES = frozenset((
    "market.html", "asia.html", "index.html", "screener.html", "screener_domains.html",
    "dart_feed.html", "watchlist.html", "daily_byte.html", "portfolio.html",
    "budget.html", "paper.html", "reddit_insider.html", "realestate.html",
    "gics_candidates.html", "errors.html",
))

_TOKEN = (os.getenv("DASHBOARD_TOKEN") or "").strip()
_AUTH_USER = (os.getenv("DASHBOARD_USER") or "").strip()
_AUTH_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "").strip()
_AUTH_REALM = "NOAH stock dashboard"

# ── gzip 압축(전송량 5~10x↓, 사용자 2026-06-28 '대시보드 느려'). 큰 HTML/JS/JSON
#    무압축 전송이 주 병목 — trade 서버 패턴 미러. 정적 서빙 + /trade 프록시 응답 적용.
_GZIP_MIN_BYTES = 1024
_GZIP_MIME_PREFIXES = ("text/html", "text/css", "application/javascript",
                       "text/javascript", "application/json", "image/svg+xml",
                       "text/plain")


def _render_note() -> str:
    """분기 인포그래픽 이미지가 없을 때의 **진짜 이유** 한 문장."""
    try:
        from bot.korean_font import diagnose, find_font
        if not find_font():
            return diagnose()
        return "이미지 렌더 실패 — 표로 표시합니다(폰트는 정상)."
    except Exception as exc:                       # noqa: BLE001
        return f"이미지 렌더 실패({type(exc).__name__}) — 표로 표시합니다."


class _CapturingWFile:
    """SimpleHTTPRequestHandler 가 쓰는 헤더+바디를 버퍼링 → 핸들러 종료 후
    Content-Type 검사·gzip 적용."""

    def __init__(self) -> None:
        self._buf = BytesIO()

    def write(self, data) -> int:
        return self._buf.write(data)

    def flush(self) -> None:
        pass

    def split(self):
        raw = self._buf.getvalue()
        sep = raw.find(b"\r\n\r\n")
        if sep == -1:
            return raw, b""
        return raw[: sep + 4], raw[sep + 4:]


def _pick_content_type(header_bytes: bytes):
    for line in header_bytes.split(b"\r\n"):
        if line.lower().startswith(b"content-type:"):
            return (line.split(b":", 1)[1].strip()
                    .decode("latin-1", errors="replace").split(";", 1)[0]
                    .strip().lower())
    return None


def _patch_headers(header_bytes: bytes, *, content_length, add: dict) -> bytes:
    """캡처된 헤더 blob 재작성 — Content-Length 교체 + add 헤더 추가(중복 제거)."""
    out = []
    add_lower = {k.lower() for k in add}
    for line in header_bytes.split(b"\r\n"):
        if not line:
            continue
        if b":" in line:
            lname = line.partition(b":")[0].decode("ascii", "replace").lower()
            if lname == "content-length" and content_length is not None:
                continue
            if lname in add_lower:
                continue
        out.append(line)
    if content_length is not None:
        out.append(f"Content-Length: {content_length}".encode("ascii"))
    for k, v in add.items():
        out.append(f"{k}: {v}".encode("latin-1", "replace"))
    return b"\r\n".join(out) + b"\r\n\r\n"

# ── lookup_detail stale-while-revalidate (종목분석 지연로딩 재방문 즉시화) ──
# 무거운 detail(collect_stock_snapshot 의 yfinance 직렬 호출 + 동종비교 최대
# 8콜 + 시장 enrichment)은 첫 cold 렌더만 ~10-30초. 그 후엔 디스크 캐시를
# 즉시 서빙하고, 만료분은 백그라운드 1회 재렌더해 다음 방문을 신선하게 유지
# (사용자 2026-06-10 '아직도 느린데'). render_lookup_detail 을 그대로 재사용
# 하므로 새 데이터-경로 코드 0 · stock_snapshot 무수정 · graceful.
import threading as _threading

_LOOKUP_DETAIL_FRESH_SEC = 1800     # 30분: 그냥 즉시 서빙
_LOOKUP_DETAIL_STALE_SEC = 86400    # 24h: stale 즉시 서빙 + 백그라운드 갱신
_LOOKUP_REFRESH_LOCK = _threading.Lock()
_LOOKUP_REFRESHING: set[str] = set()


def _atomic_write_bytes(path, data: bytes) -> None:
    """temp+os.replace 로 캐시 파일을 원자적으로 기록(반쯤 쓰인 파일 read 방지)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def _kick_lookup_detail_refresh(ticker: str, cache_f, enrich: bool = True) -> None:
    """stale 캐시를 백그라운드에서 1회 재렌더(동시 중복 방지). 실패해도 기존
    stale 캐시는 그대로 → 다음 방문이 다시 시도. 요청 경로를 막지 않음."""
    _key = f"{ticker}:{'full' if enrich else 'core'}"
    with _LOOKUP_REFRESH_LOCK:
        if _key in _LOOKUP_REFRESHING:
            return
        _LOOKUP_REFRESHING.add(_key)

    def _work():
        try:
            import time as _t
            from bot.dashboard import render_lookup_detail
            _t0 = _t.time()
            html = render_lookup_detail(ticker, enrich=enrich)
            # 백그라운드 재렌더도 같은 줄을 남긴다 — 여기만 조용하면 재방문
            # 경로의 비용이 영원히 안 보인다(#54 대조 0건은 통과가 아니다).
            try:
                import bot.stock_snapshot as _ss
                _st = _ss.last_timing(ticker)
                log.info("detail-timing %s phase=%s(bg) render=%.3fs %s",
                         ticker, "full" if enrich else "core", _t.time() - _t0,
                         " ".join(f"{k}={v}s" for k, v in _st.items())
                         or "(스냅샷 캐시 히트 — 단계 없음)")
            except Exception:                                  # noqa: BLE001
                pass
            if html:
                _atomic_write_bytes(cache_f, html.encode("utf-8"))
        except Exception as exc:
            log.debug("lookup_detail refresh %s: %s", ticker, exc)
        finally:
            with _LOOKUP_REFRESH_LOCK:
                _LOOKUP_REFRESHING.discard(_key)

    try:
        _threading.Thread(target=_work, daemon=True).start()
    except Exception:
        pass


def _trade_upstream_url(base: str, sub: str) -> str:
    """trade 프록시(/trade[/...]) 업스트림 URL.

    미디어(/media/...)는 trade 백엔드 ROOT(8765/media/...)에 있다 —
    /dashboard/ 의 자식이 아니라 형제. 그래서 /trade/media/... 는 base(기본
    …/dashboard)가 아니라 백엔드 루트로 보내야 8765/media/ 에 닿는다(안 그러면
    8765/dashboard/media → 404 → 카드 이미지 회색, 사용자 2026-06-15). **/api/**
    도 동일 — trade 백엔드의 보고서 API(/api/company_report·/api/period_report)는
    ROOT(8765/api/...)라, base(…/dashboard) 아래로 보내면 8765/dashboard/api → 404
    → 기업/전체 보고서 위젯 '네트워크 오류'(사용자 2026-06-18). 그 외는 base 아래로."""
    if sub.startswith("/media/") or sub.startswith("/api/"):
        from urllib.parse import urlsplit
        p = urlsplit(base)
        return f"{p.scheme}://{p.netloc}" + sub
    return base.rstrip("/") + sub


def _rewrite_trade_html(body: bytes, token: str) -> bytes:
    """프록시된 trade HTML/CSS/JS 본문 경로 보정:

    - 절대 /dashboard/ → /trade/ (마운트 정렬).
    - 상대 ../media/ → /<token>/trade/media/ (토큰 포함 절대). trade HTML 은
      미디어를 상대 ../media/ 로 참조하는데, /trade/ 프록시 마운트 아래선 ../ 가
      /<token>/media/(NOAH 루트 → 404)로 탈출해 카드가 회색이 된다(사용자
      2026-06-15). 토큰 포함 절대로 바꾸면 트레일링슬래시 무관하게 /trade/media/
      로 해석되고 토큰 보존 → _trade_upstream_url 가 8765/media/ 로 매핑해 200
      JPEG. 8765 직접 접속(원본 ../media/)은 프록시를 안 거쳐 그대로 정상."""
    body = body.replace(b"/dashboard/", b"/trade/")
    mpfx = (f"/{token}".encode() if token else b"") + b"/trade/media/"
    return body.replace(b"../media/", mpfx)


# ── 유동성 보드 실시간 오버레이 소스 ──────────────────────────────────
# 환율(_FX_SOURCE)과 같은 패턴의 확장(사용자 2026-08-20 "VIX 나 코인도
# 환율처럼") — FRED 는 1영업일 지연 종가라, 실시간 소스가 있는 시리즈만
# 클라 JS 가 5분마다 최신값·기준일을 덮는다(기간지표 1M/3M/YoY·차트는
# FRED 히스토리 유지 — 두 정의를 섞지 않는 기존 규약).
#   vix    → 네이버 world index .VIX (메인 대시보드·시장타이밍과 canonical
#            동일 소스 — 2026-07-26 'VIX 가 화면마다 다름' 재발 방지)
#   btcusd/ethusd → yfinance BTC-USD/ETH-USD. ⚠️ 네이버 코인은 **업비트
#            원화**라 FRED CBBTCUSD(USD) 옆에 두면 통화가 충돌(#34) —
#            USD 를 주는 yfinance 를 쓴다(크립토는 24/7 실시간 호가).
#   dgs10/dgs30 → yfinance ^TNX/^TYX(CBOE 국채수익률 지수). ⚠️ 야후가
#            수익률(4.23) 또는 ×10 지수(42.3) 어느 스케일로 주는지 원천마다
#            달라 **클라 JS 가 FRED 값과 대조해 스케일을 판정**하고, 정규화
#            후에도 FRED 와 ±25% 넘게 어긋나면 버린다(빈칸>틀린값). 2Y 는
#            야후 무지수·선물뿐이라 제외, 스프레드는 파생이라 제외(#32),
#            DXY 는 FRED 광범위지수와 **다른 시리즈**라 제외(#34).
_LIVE_SOURCE = {"vix": ("nvidx", ".VIX"),
                "btcusd": ("yf", "BTC-USD"),
                "ethusd": ("yf", "ETH-USD"),
                "dgs10": ("yf", "^TNX"),
                "dgs30": ("yf", "^TYX")}
_live_cache: dict = {}          # {key: (ts, payload)} — 30초 in-process
_LIVE_TTL_S = 30


def live_quote(key: str) -> dict:
    """{rate, change, pct, src} 또는 {} — 실패 시 클라가 FRED 값 유지(graceful).
    naver 30초 캐시는 클라이언트 내장, yfinance 는 여기 30초 캐시."""
    import time as _t
    kind, code = _LIVE_SOURCE[key]
    c = _live_cache.get(key)
    if c and _t.time() - c[0] < _LIVE_TTL_S:
        return c[1]
    out: dict = {}
    try:
        if kind == "nvidx":
            from bot.naver_marketindex import fetch_world_indices
            rec = (fetch_world_indices((code,)) or {}).get(code) or {}
            if rec.get("close") is not None:
                out = {"rate": rec["close"], "change": rec.get("change"),
                       "pct": rec.get("pct"), "src": "네이버 실시간"}
        elif kind == "yf":
            import yfinance as yf
            fi = yf.Ticker(code).fast_info
            last = getattr(fi, "last_price", None)
            prev = getattr(fi, "previous_close", None)
            if last:
                chg = (last - prev) if prev else None
                pct = (chg / prev * 100.0) if (chg is not None and prev) else None
                out = {"rate": float(last),
                       "change": (round(chg, 2) if chg is not None else None),
                       "pct": (round(pct, 2) if pct is not None else None),
                       "src": "yfinance 실시간"}
    except Exception as exc:                                   # noqa: BLE001
        log.warning("live_quote(%s): %s", key, exc)
    _live_cache[key] = (_t.time(), out)
    return out


def _wrap_style() -> str:
    """분기실적 탭 전체를 감쌀 다크 컨테이너 스타일. 실패해도 화면은 살아야
    하므로 빈 문자열로 폴백(그때는 조각별 배경만 보인다)."""
    try:
        from bot.dart_production import qwrap_style
        return qwrap_style()
    except Exception as exc:                                   # noqa: BLE001
        log.debug("wrap_style: %s", exc)
        return ""


# 차트 payload 디스크 캐시 파일명. **순수 함수로 빼 둔다** — 회귀가 소스
# 문자열을 슬라이스하면 같은 계약을 지키는 리팩터에 깨진다(#19).
_CHART_CACHE_VER = 6


def chart_cache_name(safe: str, interval: str, rng: str,
                     lite: bool = False) -> str:
    """payload 모양이 바뀌면 `_CHART_CACHE_VER` 를 올려 옛 캐시를 무시한다.

    ⚠️ `lite`(기본 OFF 오버레이 생략)는 **다른 payload** 라 파일도 따로다 —
    같은 파일에 섞으면 첫 화면이 lite 를 굽고 그 뒤 TTL 내내 오버레이를
    켜도 데이터가 없다.
    """
    return (f"{safe}_{interval}_{rng}_v{_CHART_CACHE_VER}"
            + ("_lite" if lite else "") + ".json")


def _production_html(ticker: str, payload: dict) -> str:
    """분기실적 탭의 주요 제품 + 생산능력·가동률 표 HTML. 부재는 ""(섹션 생략).

    분기 시계열의 최신 보고서부터 거슬러 표가 있는 첫 보고서를 쓴다 —
    새 보고서가 나오면 자동 롤링(dart_production.production_rolling)."""
    try:
        from bot.market import detect_market
        if detect_market(ticker) != "KR":
            return ""                      # 원천이 DART 라 KR 전용
        qs = payload.get("quarters") or []
        if not qs:
            return ""
        from bot.dart_client import get_dart
        from bot.dart_production import (render_html, render_products_html,
                                         tables_rolling)
        # ⚠️ 보고서를 **한 번만** 걷는다. 표마다 따로 걸으면 같은 문서를 표
        # 수만큼 다시 받고, 표가 없는 종목(스윕 실측상 다수)에서는 그게
        # 분기수×상한수×표수로 곱해진다(2026-08-21 실측 8건 → 2건).
        got = tables_rolling(get_dart(), ticker, qs)
        # 사용자 2026-08-21 "가동률 표 위에" — 주요 제품 표가 먼저.
        return (render_products_html(got.get("products"))
                + render_html(got.get("production")))
    except Exception as exc:                                   # noqa: BLE001
        log.debug("production_html(%s): %s", ticker, exc)
        return ""


def _f_pos(v) -> bool:
    """양수인 숫자인가. FnGuide 는 '정의 불가'를 0 으로 보낸다 — 0 은 값이 아니다."""
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def _fnguide_ratio_rows(block: dict | None) -> tuple[list, dict]:
    """FnGuide 한 지표(PER 또는 PBR) 블록 → ([행], 최고밴드선 map).

    ⚠️ PER·PBR 은 밴드선만 다르고 **주가 시계열은 하나**이며 되뽑는 식도 같다
    — 두 벌로 적으면 한쪽만 고쳐져 갈라진다(#38). 여기 한 곳에서만 만든다.
    행의 비율 값은 키 이름을 `per` 로 통일한다(요약·렌더가 그 키를 본다) —
    PBR 행에서도 `per` 는 '그 지표의 배수'라는 뜻이다.

    ⚠️ FnGuide 의 x축은 **월말 버킷**이라 진행 중인 달도 그 달 마지막 날로
    찍혀 온다(사용자 2026-08-22: "아직 2026.08.31 이 안됐는데 해당 월
    마지막날로 그냥 넣은거야?"). 오지 않은 날짜를 관측일로 적으면 표가
    거짓말한다 — **오늘(KST)로 잘라** 적는다. 값 자체는 원천 것 그대로다.
    """
    mult = (block or {}).get("mult") or []
    price = (block or {}).get("price") or []
    bands = (block or {}).get("bands") or []
    if len(mult) < 4 or not price or not bands:
        return [], {}
    import datetime as _dt
    # 서버 로컬타임 의존 금지 — KST 로 명시 계산(CLAUDE.md 규칙 10a).
    _today = _dt.datetime.now(
        _dt.timezone(_dt.timedelta(hours=9))).strftime("%Y-%m-%d")
    top = {int(x[0]): x[1] for x in (bands[0] or []) if x and x[1] is not None}
    rows = []
    for x in price:
        if not x or x[1] is None:
            continue                       # 미래 구간(전망)은 주가가 없다
        b = top.get(int(x[0]))
        if not b:
            continue
        d = _dt.datetime.utcfromtimestamp(int(x[0]) / 1000).strftime("%Y-%m-%d")
        d = min(d, _today)                 # 월말 버킷 → 오지 않은 날짜 금지
        rows.append({"period": d, "price": round(float(x[1]), 2),
                     "eps": None, "per": round(mult[0] * float(x[1]) / b, 2)})
    return (rows, top) if len(rows) >= 4 else ([], top)


def _kr_band_basis_note(rows: list) -> str:
    """밴드 4선의 기준을 **측정해서**, 짧게 말한다(추측 금지 · 근거 숫자 유지).

    ⚠️ 옛 문구는 판정과 그 근거를 두 번 적어 두 줄이 넘었다(사용자 2026-08-23
    "첫번째 Remark 를 좀 더 다듬어줘. 간결하고 깔끔하게"). 줄인 건 **말**이지
    근거가 아니다 — 몇/몇 구간인지는 그대로 싣는다(#55 파생 판정은 산식을).
    """
    from bot.per_band import eps_cadence, eps_cadence_stats
    cad, _why = eps_cadence(rows)
    flat, steps = eps_cadence_stats(rows)
    tail = " · 요약은 우리 월말 관측 분포"
    if cad == "계단형":
        return f"FnGuide 밴드선 · 분모 = 분기 확정 EPS({flat}/{steps} 유지){tail}"
    if cad == "연속형":
        return (f"FnGuide 밴드선 · 분모 = 매달 갱신 EPS"
                f"({steps - flat}/{steps} 변동){tail}")
    return f"FnGuide 밴드선 · 분모 = 원천 EPS(표본 부족으로 미판정){tail}"


def _kr_denom_label(rows: list) -> str:
    """분모 이름 — 원천이 계단형이면 확정 EPS, 아니면 그렇게 안 우긴다."""
    from bot.per_band import eps_cadence
    cad, _why = eps_cadence(rows)
    if cad == "계단형":
        return "분기 확정 EPS"
    if cad == "연속형":
        return "원천 EPS(매달 갱신)"
    return "원천 EPS"


def _fnguide_ratio_table(block: dict | None, *, kind: str, px_now) -> dict | None:
    """FnGuide 한 지표 블록 → 표 payload(요약 + 밴드 4선 + 이력). 자체계산 0.

    ⚠️ 값은 FnGuide 가 준 것을 그대로 되뽑는다 — 우리가 다시 계산하면 같은
    탭의 차트와 표가 갈라진다(#38 산식은 한 곳). 주가 시계열의 각 시점에서
    그 시점 밴드선(최고 배수)의 비율로 그때의 배수를 되짚는다:
        배수(t) = 최고배수 × 주가(t) / 최고밴드선(t)
    (밴드선은 '그 배수에서의 적정주가'이므로 비율이 곧 배수다.)
    """
    mult = (block or {}).get("mult") or []
    price = (block or {}).get("price") or []
    rows, top = _fnguide_ratio_rows(block)
    if not rows:
        return None
    from bot.per_band import summary as _sm
    labels = ("최고", "중상", "중하", "최저")
    last_obs = rows[-1]["price"]
    top_last = top.get(int(price[-1][0])) or 0.0
    # ⚠️ FnGuide 는 **정의 불가**(적자 등)인 배수를 0 으로 채워 보낸다 —
    # 차트는 이미 그 선을 안 그리는데(`mult<=0` 제외) 표만 `0.00x · 0.00` 을
    # 찍고 있었다(사용자 캡처의 '최저 0.00x'). 0 은 값이 아니라 '없음'이므로
    # 행을 **뺀다**(빈칸이 틀린 숫자보다 낫다) — 대신 뺐다는 사실을 말한다(#43).
    # 그 배수가 **실제로 있었던 시점**(사용자 2026-08-22). KR 은 배수를 원천이
    # 주므로 우리 월말 관측과 딱 맞지 않을 수 있다 — 그때는 비운다(#32).
    from bot.per_band import band_period as _bp, _at_fields as _atf
    _obs = [(r["period"], r["price"], None, r["per"]) for r in rows]
    _bands = [{"label": labels[i], "mult": round(float(mult[i]), 2),
               "fair": (round(top_last * float(mult[i]) / float(mult[0]), 2)
                        if mult[0] and top_last else None),
               **_atf(_bp(_obs, round(float(mult[i]), 2)))}
              for i in range(4) if _f_pos(mult[i])]
    _dropped = [labels[i] for i in range(4) if not _f_pos(mult[i])]
    return {"rows": rows, "kind": kind,
            "bands": _bands,
            "bands_note": ("원천이 " + "·".join(_dropped) + " 배수를 주지 않아"
                           " 제외했습니다(적자 등으로 정의 불가)."
                           if _dropped else None),
            "eps_now": None, "n": len(rows), "price_now": px_now or last_obs,
            # ⚠️ 밴드 4선은 **FnGuide 가 준 값**이지 우리 월말 관측 분포가
            # 아니다 — 화면이 '관측 N개 분포'라고 적으면 거짓말이다(#55).
            # ⚠️⚠️ 그리고 그 기준을 **재서** 말한다. 2026-08-22 에 나는 근거
            # 없이 "분기 실적 기준" 이라고 적었는데, 화면 값으로 되짚으니
            # EPS 가 매달 +81~82 로 **선형**이었다(분기 확정치면 계단이어야
            # 한다). 원천이 무엇을 쓰는지는 추측이 아니라 측정이다.
            "band_basis": _kr_band_basis_note(rows),
            "denom_label": _kr_denom_label(rows),
            "summary": _sm(rows, years=5, price_now=px_now),
            "source": "FnGuide 밴드차트(네이버 임베드) — 차트와 같은 값",
            "basis": "fnguide", "market": "KR"}


def _kr_band_tables(band: dict | None,
                    ticker: str = "") -> tuple[dict | None, dict | None]:
    """FnGuide 밴드 payload → (PER 표, PBR 표). 현재가는 **1콜만**.

    ⚠️ PBR 은 사용자 2026-08-22 "PBR 밴드도 같은 요약을 추가해줘. 이건 한국만
    하면 돼" — FnGuide 가 KR 만 밴드선을 주므로 해외는 원천이 없다. 없는 걸
    자체계산으로 메우지 않는다.

    ⚠️ `ticker` 를 주면 **현재가 1콜**이 붙는다(요약의 현재 배수). 밴드 자체는
    월 해상도 + 12h 캐시라 그것 없이는 오늘 움직임이 반영되지 않는다. 두 표가
    **같은 현재가**를 써야 한다 — 따로 부르면 장중에 두 요약이 갈라진다.

    ⚠️ **한쪽이 없다고 다른 쪽을 죽이지 말 것**(2026-08-22 SKC 011790.KS 실측):
    적자 기업은 FnGuide 가 PER 밴드선을 0(정의 불가)으로 보내 PER 행이 안
    만들어지는데, 옛 코드는 그때 **PBR 표까지 통째로** None 으로 돌려줬다 —
    화면의 PBR 차트는 멀쩡한데 표만 사라져 "왜 안 나와?"가 됐다.
    """
    per_b = (band or {}).get("per") or {}
    pbr_b = (band or {}).get("pbr") or {}
    # 현재가 기준은 **어느 쪽이든 행이 있는 블록**에서 잡는다(주가 시계열은
    # 어차피 하나다) — PER 이 비었다고 PBR 요약까지 현재가를 잃으면 안 된다.
    ref = (_fnguide_ratio_rows(per_b)[0] or _fnguide_ratio_rows(pbr_b)[0])
    if not ref:
        return None, None
    from bot.per_band import live_price as _lp
    px_now = _lp(ticker, ref[-1]["price"]) if ticker else None
    per_t = _fnguide_ratio_table(per_b, kind="PER", px_now=px_now)
    pbr_t = _fnguide_ratio_table(pbr_b, kind="PBR", px_now=px_now)
    _attach_roe(per_t, pbr_t)
    return per_t, pbr_t


def _attach_roe(per_t: dict | None, pbr_t: dict | None) -> None:
    """PBR 표에 ROE 열을 붙인다 — 사용자 2026-08-23 "한국기업에 한해서 여기
    PBR 옆에 ROE 를 붙여줄수 있어?".

    ROE = PBR ÷ PER 이다(둘 다 같은 주가로 나눈 값이라 주가가 약분된다:
    (P/B) ÷ (P/E) = E/B). 즉 **자체 추정이 아니라 원천 두 값의 항등식**이고,
    분모(EPS)가 원천의 것이므로 ROE 도 원천 기준을 그대로 따른다.

    ⚠️ 기간으로 **조인**한다 — 위치로 매기면 한쪽이 한 달 덜 올 때 전 행이
    밀린다(#88). 그리고 PER 이 없는 달(적자 등)은 **비운다**(#32).
    """
    if not per_t or not pbr_t:
        return
    per_by = {str(r.get("period"))[:10]: r.get("per")
              for r in (per_t.get("rows") or [])}
    n = 0
    for r in pbr_t.get("rows") or []:
        e = per_by.get(str(r.get("period"))[:10])
        b = r.get("per")
        if e and b and e > 0:
            r["roe"] = round(b / e * 100.0, 2)
            n += 1
    if n:
        pbr_t["roe_note"] = "ROE = PBR ÷ PER × 100 (같은 시점 · 원천 두 값)"


def _kr_per_table(band: dict | None, ticker: str = "") -> dict | None:
    """PER 표만(호환용 얇은 래퍼)."""
    return _kr_band_tables(band, ticker)[0]


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the archive directory; adds POST /api/delete + optional
    URL-token and Basic-Auth gating."""

    def __init__(self, *args, **kwargs):
        # `directory` keyword wires up SimpleHTTPRequestHandler's static
        # serving to the archive root regardless of CWD.
        super().__init__(*args, directory=str(_ARCHIVE_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Route stdlib's per-request log through our own logger so it
        # picks up systemd's journal formatting / log levels.
        log.info("%s - %s", self.address_string(), fmt % args)

    # ── Auth helpers ──────────────────────────────────────────────────
    def _strip_token_or_404(self) -> bool:
        """If DASHBOARD_TOKEN is set, require `/<token>` prefix. Strips
        the prefix from self.path so SimpleHTTPRequestHandler serves the
        right file. Returns True on success, sends 404 and returns False
        when the prefix is missing or wrong."""
        if not _TOKEN:
            return True
        # Accept `/<token>/` and `/<token>/<rest>`. The no-trailing-slash
        # form `/<token>` gets a 301 redirect to `/<token>/` so relative
        # URLs in the page (e.g. `fetch('api/delete')`) resolve against
        # the right base — otherwise the browser treats the page as
        # being at `/` and POSTs to `/api/delete`, missing the token
        # prefix and getting a 404. Anything else → 404 with no body
        # so a scanner can't tell whether tokens exist at all.
        candidate = f"/{_TOKEN}"
        if self.path == candidate:
            self.send_response(301)
            self.send_header("Location", candidate + "/")
            self.end_headers()
            return False
        if self.path == candidate + "/":
            self.path = "/"
            return True
        if self.path.startswith(candidate + "/"):
            self.path = self.path[len(candidate):]
            return True
        self.send_error(404, "Not Found")
        return False

    def _check_basic_auth_or_401(self) -> bool:
        """If credentials are configured, require a matching
        `Authorization: Basic` header. Returns True on success;
        on failure sends 401 + WWW-Authenticate and returns False."""
        if not (_AUTH_USER and _AUTH_PASSWORD):
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                user, _, password = decoded.partition(":")
                if (
                    compare_digest(user, _AUTH_USER)
                    and compare_digest(password, _AUTH_PASSWORD)
                ):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="{_AUTH_REALM}"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Authentication required\n")
        return False

    def _authorize(self) -> bool:
        return self._strip_token_or_404() and self._check_basic_auth_or_401()

    def end_headers(self):
        path_lower = self.path.lower().split("?")[0]
        # on-demand HTML 페이지도 no-cache — 안 하면 옛 페이지가 브라우저에
        # 캐시돼 stale(예: 신고가→상한가 변경이 안 보이던 문제, 2026-06-10).
        # .json 도 no-cache — analysis_csv.json 등 regen 산출물이 브라우저
        # 휴리스틱 캐시로 stale 되는 것 방지(성능 2026-07-03 CSV 외부화 동반).
        if (path_lower.endswith((".html", "/", ".json")) or path_lower == ""
                or path_lower in ("/earnings", "/theme", "/highlow",
                                  "/usindustry", "/ushighlow", "/usmovers",
                                  "/usprepost",
                                  "/twhighlow", "/tw52",
                                  "/jp52", "/hk52", "/kr52", "/cn52",
                                  "/hkmovers", "/jpmovers", "/cnmovers",
                                  "/jphighlow", "/nxt", "/krprepost")
                or path_lower.startswith("/lookup/")
                or path_lower == "/trade" or path_lower.startswith("/trade/")):
            # /trade* — 프록시는 매 요청 trade 백엔드로 fresh fetch(서버 캐시
            # 0)지만, no-cache 가 없으면 브라우저가 옛 HTML 을 캐시해 갱신이
            # 안 보임 → 항상 revalidate (사용자 2026-06-11 '업데이트 실시간?').
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        elif path_lower.endswith((".js", ".css")):
            # 벤더 라이브러리(lightweight-charts ~250KB·chart.umd ~205KB)는
            # 버전 고정 파일 — 매 방문 Last-Modified 재검증(304 왕복)도 저사양/
            # 고지연에서 체감됨 → 7일 캐시(성능 감사 2026-07-03). 라이브러리
            # 교체 시 파일명이 같아도 7일 내 자연 만료 + regen 은 서버측이라 무관.
            self.send_header("Cache-Control", "public, max-age=604800")
        super().end_headers()

    # ── Request handlers ─────────────────────────────────────────────
    def do_GET(self):
        """모든 GET 의 **실제 소요시간**을 남긴다.

        ⚠️ 왜(사용자 2026-08-21 "여전히 꽤 오래걸리는데... 전보다 더 걸리는것
        같아"): 우리가 계측해 온 것은 `collect_stock_snapshot`(수집기)이지
        **사용자가 기다리는 요청**이 아니다. 화면이 기다리는 건 `/api/chart`
        와 `/api/quote?full=1` 인데 그 둘은 한 번도 안 재 봤다 — 재지 않은
        것을 두고 빨라졌다/느려졌다 말할 수 없다(#79·#92).
        `journalctl -u <unit> | grep api-timing` 으로 본다.
        """
        import time as _t
        _t0 = _t.time()
        try:
            return self._do_GET_routed()
        finally:
            _ms = (_t.time() - _t0) * 1000.0
            try:
                _r = self.path.split("?", 1)[0]
                if _r.startswith("/api/") or _ms >= 1000:
                    import urllib.parse as _up
                    _q = _up.parse_qs(_up.urlparse(self.path).query)
                    _tag = " ".join(
                        f"{k}={(_q.get(k) or [''])[0]}"
                        for k in ("ticker", "interval", "range", "full")
                        if _q.get(k))
                    log.info("api-timing %s %s %.0fms", _r, _tag, _ms)
            except Exception:                                  # noqa: BLE001
                pass          # 계측이 응답을 막으면 안 된다

    def _do_GET_routed(self):
        if not self._authorize():
            return
        # /api/chart?ticker=..&interval=1d|1wk|1mo&range=1mo|3mo|6mo|ytd|1y|3y|5y|max
        # On-demand timeframe fetch for the detail-page price chart. The
        # token prefix is already stripped by _authorize() above.
        if self.path.split("?", 1)[0] == "/api/chart":
            return self._handle_chart_api()
        # /api/band?ticker=..  — FnGuide PER/PBR 밴드차트(KR 전용), 탭 lazy fetch.
        if self.path.split("?", 1)[0] == "/api/band":
            return self._handle_band_api()
        # /api/technical?ticker=..[&run=1] — 기술 분석 탭. 지표(무료) + run=1 시
        # 강세/약세 토론(Gemini 1콜, cost-gated, 캐시). 탭 클릭/실행버튼 lazy fetch.
        if self.path.split("?", 1)[0] == "/api/technical":
            return self._handle_technical_api()
        # /api/quarterly?ticker=..[&run=1] — 분기실적 탭(KR). DART 분기 숫자·
        # 인포그래픽(무료) + run=1 시 성장동력/리스크 요약(Gemini 1콜, 분기당
        # 1회 캐시). technical 과 동일 비용 게이트.
        if self.path.split("?", 1)[0] == "/api/quarterly":
            return self._handle_quarterly_api()
        # /api/usdkrw · usdjpy · usdcny · usdeur · usdgbp · usdchf — 환율
        # 실시간(네이버 marketindex, 30초 캐시 내장). 유동성 보드가 FRED
        # DEX*(1영업일 지연) 최신값을 실시간으로 덮는 용(사용자 2026-07-02
        # '환율은 네이버같은곳에서 실시간으로', 2026-07-14 엔·위안 1차 +
        # 유로·파운드·스위스프랑 2차 확장 — 대만달러는 FRED 미수록이라 히스토리
        # 없이 추가했으나 사용자 요청으로 제외).
        _fx_route = self.path.split("?", 1)[0]
        if _fx_route.startswith("/api/") and _fx_route[5:] in self._FX_SOURCE:
            return self._handle_fx_api(_fx_route[5:])
        # /api/vix · btcusd · ethusd — 유동성 보드 실시간 오버레이 확장
        # (환율과 같은 패턴, live_quote 참조).
        if _fx_route.startswith("/api/") and _fx_route[5:] in _LIVE_SOURCE:
            self._json_ok(live_quote(_fx_route[5:]))
            return
        # /api/quote?ticker=..[&full=1]  — live numbers for the detail page.
        # LIGHT (default): price-derived multiples + consensus + 52주 + 이평
        # (yfinance .info, KR KIS-first). FULL: re-snapshot heavy panes.
        if self.path.split("?", 1)[0] == "/api/quote":
            return self._handle_quote_api()
        # /api/command_result?id=<hex> — 대시보드 명령 콘솔 결과 폴링.
        if self.path.split("?", 1)[0] == "/api/command_result":
            return self._handle_command_result()
        # /earnings — monthly earnings calendar page (Finnhub).
        raw = self.path.split("?", 1)[0]
        if raw == "/earnings":
            return self._handle_earnings()
        # /theme · /highlow — 테마별 시세 · 상한가/하한가 (Naver, on-demand)
        if raw in ("/theme", "/highlow"):
            return self._handle_naver_page(raw)
        # /usindustry · /ushighlow · /usmovers · /usprepost — 미국 업종별 시세 · 52주
        # 신고가/신저가 · 급등급락 TOP30 (KR 미러 — 사용자 2026-06-10/12)
        if raw in ("/usindustry", "/ushighlow", "/usmovers", "/usprepost"):
            return self._handle_us_page(raw)
        # /twhighlow — 대만 상한가/하한가 (TWSE) · /tw52 — 52주 신고가/신저가
        # (yfinance 유니버스 백그라운드, 사용자 2026-06-13 Phase 2)
        if raw in ("/twhighlow", "/tw52"):
            return self._handle_tw_page(raw)
        # /jp52 /hk52 /kr52 /cn52 — JP/HK/KR/CN 52주 신고가/신저가
        # (CN 재도입 2026-06-17 '중국도 같은 방식으로', peer-only·슬롯 :30)
        if raw in ("/jp52", "/hk52", "/kr52", "/cn52"):
            return self._handle_intl_page(raw)
        # /hkmovers /jpmovers /cnmovers — JP/CN/HK 급등·급락 (네이버 worldstock +
        # yfinance 업종, 미국 미러, 사용자 2026-06-13 '중국·홍콩·일본은 미국따라')
        if raw in ("/hkmovers", "/jpmovers", "/cnmovers"):
            return self._handle_intl_movers(
                {"/hkmovers": "HK", "/jpmovers": "JP", "/cnmovers": "CN_A"}[raw])
        # /nxt — KR NXT 장전·장후 외국인·기관 수급 (네이버 trendForeignOrg, 2026-06-14)
        if raw == "/nxt":
            return self._handle_simple_page(
                "bot.nxt_pages", "render_nxt_page")
        # /krprepost — KR 장전·장후 시간외(단일가) 가격 급등·급락 TOP30 (네이버
        # overMarketPriceInfo, 2026-06-16). NXT(수급)와 별개 — 이건 '가격'.
        if raw == "/krprepost":
            return self._handle_simple_page(
                "bot.intl_pages", "render_kr_prepost_page")
        # /jphighlow — 일본 상한가/하한가 (구 경로, jpmovers 로 대체 — 캐시 링크 호환)
        if raw == "/jphighlow":            return self._handle_jp_stop()
        # /trade[/...] — 한국 수출입(trade) 대시보드 리버스 프록시
        if raw == "/trade" or raw.startswith("/trade/"):
            return self._handle_trade_proxy()
        # /lookup/<TICKER> — lightweight stock overview page (on-demand).
        if raw.startswith("/lookup/"):
            return self._handle_lookup()
        # /api/lookup_detail — 지연로딩: lookup shell 이 비동기로 받는 무거운 detail
        if raw == "/api/lookup_detail":
            return self._handle_lookup_detail()
        if raw.startswith("/api/search"):
            return self._handle_search_api()
        if raw == "/api/favorites":
            return self._handle_favorites_get()
        if raw == "/api/important":
            return self._handle_important_get()
        return self._serve_static_with_gzip("GET")

    def do_HEAD(self):
        if not self._authorize():
            return
        return self._serve_static_with_gzip("HEAD")

    def _serve_static_with_gzip(self, method: str) -> None:
        """정적 파일 서빙 + gzip(전송량 5~10x↓, GET 만). no-cache 는 end_headers
        오버라이드가 이미 주입하므로 여기선 Content-Encoding/Vary 만 추가. HEAD·
        gzip 미지원·소형·비대상 MIME 는 무압축 pass-through(동작 동일). HEAD 는 본문이
        없고 GET 과 Content-Length 불일치를 피하려 항상 pass-through(압축 안 함)."""
        if method == "HEAD" or "gzip" not in self.headers.get("Accept-Encoding", ""):
            return super().do_HEAD() if method == "HEAD" else super().do_GET()
        orig = self.wfile
        buf = _CapturingWFile()
        self.wfile = buf
        try:
            super().do_GET()
        finally:
            self.wfile = orig
        header_bytes, body = buf.split()
        mime = _pick_content_type(header_bytes)
        gzippable = (mime is not None
                     and any(mime.startswith(p) for p in _GZIP_MIME_PREFIXES)
                     and len(body) >= _GZIP_MIN_BYTES)
        if gzippable:
            gz = gzip.compress(body)
            orig.write(_patch_headers(
                header_bytes, content_length=len(gz),
                add={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}) + gz)
        else:
            orig.write(header_bytes + body)

    def do_POST(self):
        if not self._authorize():
            return
        if self.path == "/api/important":
            return self._handle_important_post()
        if self.path == "/api/memo":
            return self._handle_memo_post()
        if self.path == "/api/reminder":
            return self._handle_reminder_post()
        if self.path == "/api/vc_suppress":
            return self._handle_vc_suppress_post()
        if self.path == "/api/favorite_add":
            return self._handle_favorite_add()
        if self.path == "/api/favorite_remove":
            return self._handle_favorite_remove()
        if self.path == "/api/favorite_reorder":
            return self._handle_favorite_reorder()
        if self.path == "/api/screener_delete":
            return self._handle_screener_delete()
        if self.path == "/api/daily_byte_delete":
            return self._handle_daily_byte_delete()
        if self.path == "/api/blog_delete":
            return self._handle_blog_delete()
        if self.path == "/api/kg_approve":
            return self._handle_kg_approve(all_pending=False)
        if self.path == "/api/kg_approve_all":
            return self._handle_kg_approve(all_pending=True)
        if self.path == "/api/realestate_delete":
            return self._handle_simple_delete(
                "realestate_archive", r"^\d{6}_[a-zA-Z0-9_]{1,40}\.json$",
                "regenerate_realestate_index")
        if self.path == "/api/cheongyak_delete":
            # 청약 기록은 부동산 대시보드에 실린다(단독 페이지 제거, 2026-08-20).
            return self._handle_simple_delete(
                "cheongyak_archive", r"^\d{6}_[a-zA-Z0-9_]{1,40}\.json$",
                "regenerate_realestate_index")
        if self.path == "/api/screen_delete":
            return self._handle_simple_delete(
                "screen_archive", r"^\d{6}_[a-f0-9]{1,20}\.json$",
                "regenerate_screen_index")
        if self.path == "/api/portfolio_send":
            return self._handle_portfolio_send()
        if self.path == "/api/run":
            return self._handle_run()
        if self.path != "/api/delete":
            self.send_error(404, "Not Found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            date = payload.get("date") or ""
            ticker = (payload.get("ticker") or "").upper()
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid date: {date!r}")
            if not _TICKER_RE.match(ticker):
                raise ValueError(f"invalid ticker: {ticker!r}")

            day_dir = (_ARCHIVE_ROOT / date).resolve()
            # Path traversal guard: day_dir MUST be a direct child of the
            # archive root after resolve(). A crafted "date" like
            # "../../etc" would have been rejected by _DATE_RE already,
            # but the resolve()+relative_to() check is the belt+suspenders.
            try:
                day_dir.relative_to(_ARCHIVE_ROOT)
            except ValueError:
                raise ValueError("path escape attempt")

            deleted: list[str] = []
            for ext in (".json", ".html"):
                f = day_dir / f"{ticker}{ext}"
                if f.exists() and f.is_file():
                    f.unlink()
                    deleted.append(f.name)

            if not deleted:
                raise ValueError(f"no archive entry for {date}/{ticker}")

            # Refresh index / errors / detail listings so the deleted
            # card disappears on the next page load (and the stats
            # cards / accuracy denominator rebuild without it).
            try:
                regenerate_index()
            except Exception as exc:
                log.warning("delete: regen failed (file removal still applied): %s", exc)

            log.info("delete: %s/%s removed (%s)", date, ticker, ", ".join(deleted))
            self._reply_json(200, {"ok": True, "deleted": deleted})
        except ValueError as exc:
            log.warning("delete: bad request — %s", exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("delete: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_simple_delete(self, subdir: str, filename_re: str,
                              regen_fn: str | tuple[str, ...]) -> None:
        """Generic per-run JSON archive delete under ~/.tradingagents/<subdir>/
        YYYY-MM-DD/<filename>. Validates date + filename (path-traversal
        guard) then unlinks + calls bot.dashboard.<regen_fn>(). Used by
        /api/realestate_delete (and future archive surfaces).

        regen_fn 은 **여러 개**일 수 있다 — 같은 레코드를 두 화면이 읽으면
        하나만 다시 그렸을 때 다른 쪽에 지운 카드가 남는다(실수 #27)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            date = payload.get("date") or ""
            filename = payload.get("filename") or ""
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid date: {date!r}")
            import re as _re_g
            if not _re_g.match(filename_re, filename):
                raise ValueError(f"invalid filename: {filename!r}")
            from pathlib import Path as _P
            archive_root = _P.home() / ".tradingagents" / subdir
            date_dir = (archive_root / date).resolve()
            try:
                date_dir.relative_to(archive_root.resolve())
            except ValueError:
                raise ValueError("path escape attempt")
            target = date_dir / filename
            if not target.exists() or not target.is_file():
                raise ValueError(f"no entry for {date}/{filename}")
            target.unlink()
            try:
                import importlib
                _dash = importlib.import_module("bot.dashboard")
                for _fn in ((regen_fn,) if isinstance(regen_fn, str) else regen_fn):
                    getattr(_dash, _fn)()
            except Exception as exc:
                log.warning("%s: regen failed (file removed): %s", regen_fn, exc)
            log.info("simple_delete[%s]: %s/%s removed", subdir, date, filename)
            self._reply_json(200, {"ok": True, "deleted": [filename]})
        except ValueError as exc:
            log.warning("simple_delete[%s]: bad request — %s", subdir, exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("simple_delete[%s]: unexpected failure", subdir)
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_screener_delete(self) -> None:
        """POST /api/screener_delete body: {"date": "YYYY-MM-DD",
        "filename": "HHMMSS_slug.json"}. Removes the archive JSON file
        and regenerates screener.html. Mirror of /api/delete but for the
        per-run JSON archive at ~/.tradingagents/screener_archive/."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            date = payload.get("date") or ""
            filename = payload.get("filename") or ""
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid date: {date!r}")
            # Filename must match HHMMSS_<slug>.json — path traversal guard.
            import re as _re_scr
            if not _re_scr.match(r"^\d{6}_[a-zA-Z0-9_]{1,60}\.json$", filename):
                raise ValueError(f"invalid filename: {filename!r}")

            from pathlib import Path as _P
            archive_root = _P.home() / ".tradingagents" / "screener_archive"
            date_dir = (archive_root / date).resolve()
            try:
                date_dir.relative_to(archive_root.resolve())
            except ValueError:
                raise ValueError("path escape attempt")

            target = date_dir / filename
            if not target.exists() or not target.is_file():
                raise ValueError(f"no screener entry for {date}/{filename}")
            target.unlink()

            # Regen screener.html so the deleted card disappears.
            try:
                from bot.dashboard import regenerate_screener_index
                regenerate_screener_index()
            except Exception as exc:
                log.warning("screener_delete: regen failed (file removal still applied): %s", exc)

            log.info("screener_delete: %s/%s removed", date, filename)
            self._reply_json(200, {"ok": True, "deleted": [filename]})
        except ValueError as exc:
            log.warning("screener_delete: bad request — %s", exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("screener_delete: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_blog_delete(self) -> None:
        """POST /api/blog_delete {date, filename} — 블로그 아카이브 글 삭제
        + blog.html 재생성 (daily_byte_delete mirror, 사용자 2026-06-11)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            date = payload.get("date") or ""
            filename = payload.get("filename") or ""
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid date: {date!r}")
            import re as _re_bl
            if not _re_bl.match(r"^\d{6}_[0-9a-f]{4,16}\.json$", filename):
                raise ValueError(f"invalid filename: {filename!r}")
            from pathlib import Path as _P
            archive_root = _P.home() / ".tradingagents" / "blog_archive"
            date_dir = (archive_root / date).resolve()
            try:
                date_dir.relative_to(archive_root.resolve())
            except ValueError:
                raise ValueError("path escape attempt")
            target = date_dir / filename
            if not target.exists() or not target.is_file():
                raise ValueError(f"no blog entry for {date}/{filename}")
            target.unlink()
            try:
                from bot.dashboard import regenerate_blog_index
                regenerate_blog_index()
            except Exception as exc:
                log.warning("blog_delete: regen failed (file removed): %s", exc)
            log.info("blog_delete: %s/%s removed", date, filename)
            self._reply_json(200, {"ok": True, "deleted": [filename]})
        except ValueError as exc:
            log.warning("blog_delete: bad request — %s", exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("blog_delete: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_kg_approve(self, *, all_pending: bool) -> None:
        """POST /api/kg_approve {company,relation,target} (개별반영) ·
        /api/kg_approve_all {} (전체반영) — 관계후보 승인(런타임, 커밋 불요).
        취급품목 → 런타임 reinforce 오버레이(수출입 레퍼런스북 즉시 병합) + 큐 '등재',
        그 외 → '승인'. blog.html 재생성. (사용자 2026-06-24 대시보드 반영 버튼)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 2048:
                raise ValueError("oversized request body")
            payload = json.loads(self.rfile.read(length)) if length else {}
            from trade import kg_candidates as _kg
            if all_pending:
                res = _kg.approve_candidates(all_pending=True)
            else:
                co = str(payload.get("company") or "").strip()
                rel = str(payload.get("relation") or "").strip()
                tgt = str(payload.get("target") or "").strip()
                if not (co and rel and tgt):
                    raise ValueError("company/relation/target required")
                res = _kg.approve_candidates(keys=[(co, rel, tgt)])
            try:
                from bot.dashboard import (regenerate_blog_index,
                                           regenerate_valuechain_index)
                regenerate_blog_index()
                regenerate_valuechain_index()
            except Exception as exc:
                log.warning("kg_approve: dashboard regen failed: %s", exc)
            log.info("kg_approve: all=%s → %s", all_pending, res)
            self._reply_json(200, {"ok": True, **res})
        except ValueError as exc:
            log.warning("kg_approve: bad request — %s", exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("kg_approve: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_daily_byte_delete(self) -> None:
        """POST /api/daily_byte_delete body: {"date": "YYYY-MM-DD",
        "filename": "HHMMSS_daily_byte.json"}. Removes the archive JSON and
        regenerates daily_byte.html. Mirror of /api/screener_delete."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            date = payload.get("date") or ""
            filename = payload.get("filename") or ""
            if not _DATE_RE.match(date):
                raise ValueError(f"invalid date: {date!r}")
            import re as _re_db
            if not _re_db.match(r"^\d{6}_[a-zA-Z0-9_]{1,60}\.json$", filename):
                raise ValueError(f"invalid filename: {filename!r}")

            from pathlib import Path as _P
            archive_root = _P.home() / ".tradingagents" / "daily_byte_archive"
            date_dir = (archive_root / date).resolve()
            try:
                date_dir.relative_to(archive_root.resolve())
            except ValueError:
                raise ValueError("path escape attempt")

            target = date_dir / filename
            if not target.exists() or not target.is_file():
                raise ValueError(f"no daily_byte entry for {date}/{filename}")
            # 임베드 인포그래픽 PNG 도 함께 삭제 (orphan 방지). JSON 의 png
            # 상대경로(archive/ 기준)를 읽어 검증 후 unlink.
            try:
                rec = json.loads(target.read_text(encoding="utf-8"))
                png_rel = (rec.get("png") or "").strip()
                if _re_db.match(r"^daily_byte_img/[\w.\-]+\.png$", png_rel):
                    png_file = (_ARCHIVE_ROOT / png_rel).resolve()
                    if png_file.relative_to(_ARCHIVE_ROOT.resolve()) and png_file.is_file():
                        png_file.unlink()
            except Exception as exc:
                log.warning("daily_byte_delete: png cleanup skipped: %s", exc)
            target.unlink()

            try:
                from bot.dashboard import regenerate_daily_byte_index
                regenerate_daily_byte_index()
            except Exception as exc:
                log.warning("daily_byte_delete: regen failed (file removal still applied): %s", exc)

            log.info("daily_byte_delete: %s/%s removed", date, filename)
            self._reply_json(200, {"ok": True, "deleted": [filename]})
        except ValueError as exc:
            log.warning("daily_byte_delete: bad request — %s", exc)
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("daily_byte_delete: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_band_api(self) -> None:
        """FnGuide PER/PBR 밴드차트 데이터(KR 전용). 모듈이 12h 디스크 캐시하므로
        여기선 검증·위임만. Read-only GET — _authorize() 게이트 통과분."""
        import urllib.parse as _uparse
        try:
            qs = _uparse.urlparse(self.path).query
            ticker = (_uparse.parse_qs(qs).get("ticker", [""])[0] or "").strip().upper()
            if not _TICKER_RE.match(ticker):
                self._reply_json(400, {"ok": False, "error": "bad ticker"})
                return
            from bot.singleflight import once as _once
            # ⚠️ 비-KR 은 FnGuide 가 못 준다(`cmp_cd` 가 6자리 국내코드) —
            # 예전엔 여기서 그냥 돌아섰다. 이제 **자체 PER 밴드**(가격 이력 ×
            # EPS 이력)를 만들어 표 + **차트**로 준다(사용자 2026-08-22 "차트
            # 아니라 표도 괜찮아" → "외국종목도 PER 밴드차트 만들수 있으면").
            # 미국은 EDGAR 10년, 그 외는 yfinance. PBR 은 BPS 이력이 없어 안
            # 만든다(사용자 "PBR 은 안해도 돼").
            if not ticker.endswith((".KS", ".KQ")):
                snap = None
                try:
                    import bot.stock_snapshot as _ss
                    snap = _ss.collect_stock_snapshot(ticker)
                except Exception as exc:                       # noqa: BLE001
                    log.debug("band_api: 스냅샷 생략 %s: %s", ticker, exc)
                from bot.per_band import for_ticker as _pb
                tbl, why = _once(f"perband:{ticker}", lambda: _pb(ticker, snap))
                if not tbl:
                    # **왜** 없는지 말한다 — 침묵이 최악이다(#43). 사유는
                    # 경우마다 다르므로 하나로 단정하지 않는다(#50·#129).
                    self._reply_json(200, {
                        "ok": False,
                        "error": why or "PER 밴드를 만들 재료가 부족합니다."})
                    return
                # 차트는 표와 **같은 rows** 에서 나온다 — 갈라질 수 없다(#38).
                ch = tbl.get("chart")
                self._reply_json(200, {
                    "ok": True, "per_table": tbl,
                    "band": ({"per": ch, "pbr": None,
                              "csym": tbl.get("csym") or "$"} if ch else None)})
                return
            from bot.fnguide_bandchart import fetch_band_chart
            # 같은 티커의 밴드 요청이 동시에 두 번 들어온다(2026-08-21 실측:
            # 376300.KQ 2116ms / 1982ms 동시). 하나만 돌린다.
            data = _once(f"band:{ticker}", lambda: fetch_band_chart(ticker))
            if not data:
                self._reply_json(200, {"ok": False, "error": "no data"})
                return
            # ⚠️ KR 도 밴드 **차트만** 주고 표는 없었다 — 사용자 2026-08-22
            # "한국꺼도 Band 만 만들지말고 같은 탭에 표도" → PBR 도 같은 구성
            # 으로 PER 밑에. FnGuide 가 준 밴드선·주가를 그대로 되뽑으므로
            # 자체계산은 없고, 현재 배수용 **현재가 1콜**만 붙는다.
            # 표에도 라이브 시세가 붙으므로(현재 배수) 동시 요청을 하나로
            # 묶는다 — 밴드 payload 는 12h 캐시라 표만 두 번 도는 걸 막는다.
            _t = _once(f"perband:{ticker}",
                       lambda: _kr_band_tables(data, ticker))
            # ⚠️ y축 통화는 **payload 에 실어** 보낸다 — 화면 기본값('₩')에
            # 기대면 그 기본값이 바뀌는 날 국내 축이 조용히 틀린다(#55).
            from bot.per_band import currency_symbol as _cs
            self._reply_json(200, {
                "ok": True, "band": {**data, "csym": _cs(ticker)},
                "per_table": _t[0], "pbr_table": _t[1]})
        except Exception as exc:
            log.warning("band_api: failed — %s", exc)
            self._reply_json(500, {"ok": False, "error": "internal"})

    def _handle_quarterly_api(self) -> None:
        """분기실적 탭(KR 전용). ?ticker= → DART 분기 숫자 + 인포그래픽 PNG
        (무료). &run=1 → 성장동력/리스크 요약(Gemini 1콜, 분기 보고서 단위
        캐시라 같은 분기 재조회는 무과금). Read-only GET — _authorize() 게이트
        통과분. _handle_technical_api 패턴 mirror."""
        import time
        import urllib.parse as _uparse
        try:
            qs = _uparse.parse_qs(_uparse.urlparse(self.path).query)
            ticker = (qs.get("ticker", [""])[0] or "").strip().upper()
            run = qs.get("run", ["0"])[0] == "1"
            if not _TICKER_RE.match(ticker):
                self._reply_json(400, {"ok": False, "error": "bad ticker"})
                return
            from bot.market import detect_market as _dm
            from bot.quarterly_infographic import SUPPORTED_MARKETS as _SM
            _mkt = _dm(ticker)
            if _mkt not in _SM:
                self._reply_json(200, {"ok": False,
                                       "error": "분기 손익 데이터 미지원 시장"})
                return
            # 시총·PER 은 **이미 수집된 스냅샷이 있을 때만** 사용한다.
            # collect_stock_snapshot 은 cold 면 yfinance 직렬 수집으로 10~30초가
            # 걸려(이 파일 상단 주석 참조) 무료 탭-오픈 경로를 막는다 —
            # 120초 in-process 캐시가 warm 일 때만 읽고(상세 페이지를 방금
            # 렌더했으면 warm), cold 면 건너뛴다(스냅샷 전용 필드인
            # forwardEps 만 '—'로 빠질 뿐 분기 숫자·차트·시총은 정상).
            # ⚠️ 옛 게이트에 있던 `run` 항은 뺐다 — 성장동력·리스크가 탭
            # 열자마자 자동 실행되도록 바뀌어(사용자 2026-08-16) run 이 늘
            # 1 이라, 그대로 두면 모든 탭-오픈이 cold 수집 10~30초를 탄다.
            # 시총·주가는 이제 quarterly_infographic 이 라이브로 직접 받는다.
            snap = None
            _t_snap0 = time.time()
            try:
                import bot.stock_snapshot as _ss
                with _ss._SNAP_CACHE_LOCK:
                    ent = _ss._SNAP_CACHE.get(ticker)
                    warm = bool(ent and (time.time() - ent[0]) < _ss._SNAP_CACHE_TTL)
                # 비-KR 은 스냅샷이 **유일한 데이터 소스**(yfinance 분기 손익)라
                # warm 게이트가 곧 '데이터 없음'이 된다 → 항상 수집한다.
                if warm or _mkt != "KR":
                    snap = _ss.collect_stock_snapshot(ticker)
            except Exception as exc:
                log.debug("quarterly_api: snapshot skipped — %s", exc)
            _t_snap = time.time() - _t_snap0
            from bot import quarterly_infographic as _qi
            from bot.singleflight import once as _once
            _t0 = time.time()
            # ⚠️ 같은 티커의 분기실적 요청이 **같은 밀리초에 두 번** 들어온다
            # (2026-08-21 실측: USDE 10325ms / 10327ms). DART 조회·렌더가
            # 통째로 두 번 돈다 — 하나만 돌리고 결과를 나눠 준다.
            # LLM 실행 여부(run)가 다르면 다른 작업이므로 키에 포함한다.
            res = _once(f"quarterly:{ticker}:{int(bool(run))}",
                        lambda: _qi.get_or_render(ticker, snap, run_llm=run))
            # 이번 실행 비용 — 종목분석(archive 의 cost_krw 스탬프)과 동일
            # 방식·동일 sink(usage.jsonl). 무료 경로(run=0)는 LLM 콜이 없어
            # 0 이 정상. 사용자 2026-08-16 '할때마다 얼마인지'.
            run_cost_krw = 0
            try:
                from bot.usage_tracker import sum_subsystem_cost_krw
                run_cost_krw = sum_subsystem_cost_krw(
                    "quarterly_infographic", _t0)
            except Exception as exc:
                log.debug("quarterly_api: cost stamp skipped — %s", exc)
            if not res.get("ok"):
                # ⚠️ 실패해도 **어디서 시간이 갔는지**는 남겨야 한다 —
                # 조용히 끝나면 실패한 요청만 영원히 미계측이다(#54).
                try:
                    _qk = _qi.timing_key(ticker, run)
                    _qi._RENDER_TIMING.set(_qk, "h.snapshot", _t_snap)
                    _s2 = _qi.last_render_timing(ticker, run)
                    if _s2:
                        log.info("quarterly-timing %s %s (실패)", ticker,
                                 " ".join(f"{k}={v}s" for k, v in _s2.items()))
                except Exception:                              # noqa: BLE001
                    pass
                res = dict(res)
                res["cost_krw"] = run_cost_krw
                self._reply_json(200, res)
                return
            payload = res.get("payload") or {}
            img = res.get("image")
            # 📦 제품 표 + 🏭 가동률 표 — **핸들러가 직접 하는 일**이라
            # `get_or_render` 밖이다. 2026-08-22 실측: quarterly-timing 이
            # total=58.4s 인데 요청은 115초였고, 그 57초가 무엇인지 로그가
            # 답하지 못했다(#44 '기준 미기록'의 계측판).
            _t_ph = time.time()
            _prod_html = _production_html(ticker, payload)
            _t_ph = time.time() - _t_ph
            try:      # ⚠️ `_RENDER_TIMING.start` 가 렌더 시작에 지우므로
                      # 핸들러 단계는 **끝난 뒤에** 심는다(#69)
                _qk = _qi.timing_key(ticker, run)
                _qi._RENDER_TIMING.set(_qk, "h.snapshot", _t_snap)
                _qi._RENDER_TIMING.set(_qk, "h.production_html", _t_ph)
                _st = _qi.last_render_timing(ticker, run)
                if _st:
                    log.info("quarterly-timing %s %s", ticker,
                             " ".join(f"{k}={v}s" for k, v in _st.items()))
            except Exception:                                  # noqa: BLE001
                pass
            out = {
                "ok": True,
                # 아카이브 루트가 정적 서빙되므로 상대경로만 넘기면 된다
                # (프런트가 NOAH_BASE 를 붙임). 렌더 실패 시 None → 표 폴백.
                # ?v=mtime — AI 카드 생성 후 같은 파일명으로 다시 그리므로,
                # 캐시 버스터가 없으면 브라우저가 생성 전 이미지를 계속
                # 보여준다(유료 실행이 반영 안 된 것처럼 보임).
                "image_url": (f"quarterly_infographic_img/{Path(img).name}"
                              f"?v={int(Path(img).stat().st_mtime)}"
                              if img else None),
                "table_html": ("" if img else _qi.table_html(payload)),
                # 이미지가 없으면 **왜** 없는지 화면이 말해야 한다 — 옛 문구는
                # 무조건 "서버 한글 폰트 미설치" 라 폰트가 멀쩡한데 다른 이유로
                # 실패한 경우까지 오진했다(사용자 2026-08-18 LPK.DE).
                "render_note": ("" if img else _render_note()),
                # 📦 주요 제품 및 서비스 + 🏭 생산능력·생산실적·가동률 표.
                # DART 정기보고서 본문 표를 원본 구조 그대로. KR 전용(원천이
                # DART), 없으면 빈 문자열이라 프런트가 섹션을 통째로 생략한다.
                "production_html": _prod_html,
                # 하단 조각(수주잔고·재고자산·TTM) = **별도 PNG**(2026-08-21).
                # 사용자 배치: [지표·차트] → 제품 표 → 가동률 표 →
                # [수주잔고·재고자산] → [성장동력 카드] → 출처·면책(HTML).
                # 표가 이 조각 위로 와야 해서 본 이미지를 둘로 나눴다.
                "image_bottom_url": (
                    f"quarterly_infographic_img/{Path(_ib).name}"
                    f"?v={int(Path(_ib).stat().st_mtime)}"
                    if (_ib := res.get("image_bottom")) else None),
                # 출처·면책 줄 = **HTML 최하단**(2026-08-21). PNG 안에 두면
                # 카드 조각보다 위로 올라가 면책이 마지막이 아니게 된다.
                "provenance": list(_qi.provenance_line(payload)),
                # 전체를 엮는 하나의 다크 컨테이너 스타일(사용자 2026-08-21
                # "중간에 흰색부분없이 하나의 검정테두리로 전체를 엮어줘").
                # 색은 인포그래픽 팔레트 단일 출처에서 온다(#38).
                "wrap_style": _wrap_style(),
                # 성장동력·리스크 카드 = **별도 PNG**(사용자 2026-08-20).
                "cards_image_url": (
                    f"quarterly_infographic_img/{Path(_ci).name}"
                    f"?v={int(Path(_ci).stat().st_mtime)}"
                    if (_ci := res.get("cards_image")) else None),
                "growth_risk": payload.get("growth_risk") or {"ok": False},
                "latest": (payload.get("quarters") or [{}])[-1].get("label"),
                "cached": res.get("cached"),
                # 성장동력·리스크는 DART 원문 전용 — 비-KR 은 프런트가
                # 버튼을 아예 숨긴다(누를 수 없는 버튼 노출 금지).
                "llm_supported": bool(payload.get("llm_supported")),
                "source_label": payload.get("source_label") or "",
                "cost_krw": run_cost_krw,
            }
            self._reply_json(200, out)
        except Exception as exc:
            log.warning("quarterly_api: failed — %s", exc)
            self._reply_json(500, {"ok": False, "error": "internal"})

    def _handle_technical_api(self) -> None:
        """기술 분석 탭. ?ticker= → 검증 지표 세트(무료). &run=1 → 강세/약세 토론
        (Gemini 1콜, cost-gated, 티커+KST날짜 캐시). run 없으면 오늘 캐시분만 동봉
        (재과금 없음). Read-only GET — _authorize() 게이트 통과분."""
        import urllib.parse as _uparse
        try:
            qs = _uparse.parse_qs(_uparse.urlparse(self.path).query)
            ticker = (qs.get("ticker", [""])[0] or "").strip().upper()
            run = qs.get("run", ["0"])[0] == "1"
            if not _TICKER_RE.match(ticker):
                self._reply_json(400, {"ok": False, "error": "bad ticker"})
                return
            from bot import technical_analysis as _ta
            ind = _ta.compute_indicators(ticker)
            if not ind:
                self._reply_json(200, {"ok": False, "error": "지표 데이터 없음"})
                return
            debate = _ta.run_debate(ticker, ind) if run else _ta.cached_debate(ticker)
            self._reply_json(200, {"ok": True, "indicators": ind, "debate": debate})
        except Exception as exc:
            log.warning("technical_api: failed — %s", exc)
            self._reply_json(500, {"ok": False, "error": "internal"})

    def _handle_chart_api(self) -> None:
        """Serve an on-demand chart payload for a ticker / interval / range.
        Validates inputs, 1h disk-caches per (ticker, interval, range), and
        fetches via yfinance (free). Read-only GET — gated by _authorize()."""
        import time
        import urllib.parse as _uparse

        _VALID_INTERVALS = {"5m", "15m", "30m", "1h", "1d", "1wk", "1mo"}
        _VALID_RANGES = {"1d", "1wk", "1mo", "3mo", "6mo", "ytd", "1y", "3y", "5y", "max"}
        try:
            qs = _uparse.urlparse(self.path).query
            params = _uparse.parse_qs(qs)
            ticker = (params.get("ticker", [""])[0] or "").strip().upper()
            interval = (params.get("interval", ["1d"])[0] or "1d").strip()
            rng = (params.get("range", ["1y"])[0] or "1y").strip()
            if not _TICKER_RE.match(ticker):
                self._reply_json(400, {"ok": False, "error": "bad ticker"})
                return
            if interval not in _VALID_INTERVALS:
                interval = "1d"
            if rng not in _VALID_RANGES:
                rng = "1y"

            # 1h disk cache. Key is path-safe by construction (ticker passed
            # _TICKER_RE; interval/range are whitelisted literals).
            cache_dir = _ARCHIVE_ROOT.parent / "chart_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = ticker.replace(".", "_").replace("-", "_")
            # Cache version — bump when fetch_chart_payload shape changes so
            # stale caches (e.g. pre-Bollinger/MACD payloads) are ignored.
            # v3: added last_price for ~15min-delayed intraday (2026-06-04).
            # v4: last_price now validated vs the close series (bad fast_info
            #     quote → last-close fallback) — drop v3 caches with the glitch.
            # v5: elliott(피보나치·파동 오버레이) + interval_fallback 안내 필드
            #     추가 (2026-07-29) — 옛 캐시엔 없어 배포 직후 오버레이가 안
            #     보이거나 폴백 안내가 빠지는 것 방지.
            # v6: ichimoku(일목균형표 5선 + 자체 확장 시간축 + 신호) · disparity
            #     (이격도 20/60 + 밴드) 필드 추가 (2026-07-31).
            # ⚠️ lite(기본 OFF 오버레이 생략)는 **다른 payload** 라 캐시 파일도
            # 따로. 같은 파일에 섞으면 첫 화면이 lite 를 굽고 그 뒤 5분간
            # 오버레이를 켜도 데이터가 없다.
            lite = (params.get("lite", ["0"])[0] == "1")
            cache_f = cache_dir / chart_cache_name(safe, interval, rng, lite)
            # TTL 5 min — last_price 가 장중 갱신되도록. yfinance 호출은
            # 종목당 5분당 1회 → 단일 채널 audience 면 무료한도 안전 (~2000/h).
            if cache_f.exists() and (time.time() - cache_f.stat().st_mtime) < 300:
                try:
                    self._reply_json(200, json.loads(cache_f.read_text("utf-8")))
                    return
                except Exception:
                    pass  # corrupt cache → refetch

            from bot.chart_data import (fetch_chart_payload,
                                        last_chart_timing, timing_key)
            from bot.singleflight import once as _once
            # ⚠️ 같은 요청이 **같은 밀리초에 두 번** 들어온다(2026-08-21 실측).
            # 디스크 캐시는 끝난 뒤에만 도와주므로 진행 중인 중복은 못 막는다
            # — 하나만 돌리고 결과를 나눠 준다.
            payload = _once(
                f"chart:{cache_f.name}",
                lambda: fetch_chart_payload(ticker, interval=interval,
                                            period=rng, lite=lite))
            try:                      # 어디서 시간이 나는지 같이 남긴다(#69)
                _st = last_chart_timing(timing_key(ticker, interval, rng,
                                                   lite))
                if _st:
                    log.info("chart-timing %s %s/%s%s %s", ticker, interval,
                             rng, " lite" if lite else "",
                             " ".join(f"{k}={v}s" for k, v in _st.items()))
            except Exception:                                  # noqa: BLE001
                pass
            if not payload:
                # 200 (not 404) so the client can distinguish "endpoint exists
                # but no data" from "endpoint missing (old server) → static 404".
                self._reply_json(200, {"ok": False, "error": "no data"})
                return
            body = {"ok": True, "chart": payload}
            try:
                cache_f.write_text(
                    json.dumps(body, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
            self._reply_json(200, body)
        except Exception as exc:
            log.warning("chart_api: failed — %s", exc)
            self._reply_json(500, {"ok": False, "error": "internal"})

    def _handle_quote_api(self) -> None:
        """Serve a fresh live-quote payload for the detail page. LIGHT
        (default) returns pre-formatted price-derived numbers (yfinance
        .info + KR KIS-first), 5-min disk cache. FULL (?full=1) re-renders
        the heavy filing-/daily-cadence panes via collect_stock_snapshot,
        30-min cache. Read-only GET, gated by _authorize(). ₩0 — no LLM."""
        import time
        import urllib.parse as _uparse

        try:
            qs = _uparse.urlparse(self.path).query
            params = _uparse.parse_qs(qs)
            ticker = (params.get("ticker", [""])[0] or "").strip().upper()
            full = (params.get("full", ["0"])[0] or "0").strip() == "1"
            debug = (params.get("debug", ["0"])[0] or "0").strip() == "1"
            if not _TICKER_RE.match(ticker):
                self._reply_json(400, {"ok": False, "error": "bad ticker"})
                return

            # Diagnostic mode: probe every news/research/consensus source
            # and report per-source reachability. Auth-gated like the rest
            # of the dashboard. Gives a definitive "code vs key vs blocked
            # source" answer from production without ssh. Not cached.
            if debug:
                from bot.dashboard import diagnose_detail_sources
                self._reply_json(200, {"ok": True, "diagnose": diagnose_detail_sources(ticker)})
                return

            cache_dir = _ARCHIVE_ROOT.parent / "quote_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = ticker.replace(".", "_").replace("-", "_")
            kind = "full" if full else "light"
            # ⚠️ 버전은 `dashboard._RENDER_VER` 단일 출처 — 렌더러를 고치고
            # 여기를 안 올리면 stale-while-revalidate 가 옛 HTML 을 무기한
            # 서빙한다(TTL 만료도 소용없다, 2026-08-17 실측).
            from bot.dashboard import _RENDER_VER
            cache_f = cache_dir / f"{safe}_{kind}_v{_RENDER_VER}.json"
            # FULL is slow-moving (filings quarterly, 수급 daily) → 4 h.
            # LIGHT is intraday → 5 min (matches the chart API cadence).
            ttl = 14400 if full else 300  # FULL=4h, LIGHT=5min
            if cache_f.exists() and (time.time() - cache_f.stat().st_mtime) < ttl:
                try:
                    self._reply_json(200, json.loads(cache_f.read_text("utf-8")))
                    return
                except Exception:
                    pass  # corrupt cache → refetch

            # Stale cache for FULL → serve stale instantly + let client
            # background-refresh (stale-while-revalidate pattern).  Avoids
            # the 2-10 s wait on every page load for data that is quarterly.
            stale_body = None
            if full and cache_f.exists():
                try:
                    stale_body = json.loads(cache_f.read_text("utf-8"))
                    stale_body["stale"] = True  # signal to client JS
                except Exception:
                    pass
            force = (params.get("force", ["0"])[0] or "0").strip() == "1"
            if stale_body and not force:
                self._reply_json(200, stale_body)
                return

            from bot.dashboard import build_live_quote
            # force=1(수동 🔄 / stale 백그라운드 갱신)이면 120초 스냅샷 캐시도 우회
            # 해 진짜 신선 수집. 일반 cold 로드는 캐시 재사용(중복 스냅샷 제거).
            quote = build_live_quote(ticker, full=full, force_fresh=force)
            if not quote:
                # 200 (not 404) so an old server (no endpoint → static 404)
                # is distinguishable from "endpoint exists, no live data".
                self._reply_json(200, {"ok": False, "error": "no data"})
                return
            body = {"ok": True, "quote": quote}
            try:
                cache_f.write_text(
                    json.dumps(body, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
            self._reply_json(200, body)
        except Exception as exc:
            log.warning("quote_api: failed — %s", exc)
            self._reply_json(500, {"ok": False, "error": "internal"})

    def _handle_earnings(self) -> None:
        """GET /earnings[?month=YYYY-MM] — monthly earnings calendar page."""
        import urllib.parse as _ulp
        from datetime import date as _date
        try:
            qs = _ulp.parse_qs(_ulp.urlparse(self.path).query)
            month_str = (qs.get("month", [""])[0] or "").strip()
            if month_str and re.match(r"^\d{4}-\d{2}$", month_str):
                year, month = int(month_str[:4]), int(month_str[5:7])
                if not (1 <= month <= 12 and 2000 <= year <= 2099):
                    raise ValueError("invalid month")
            else:
                today = _date.today()
                year, month = today.year, today.month
            market = (qs.get("market", [""])[0] or "kr").strip().lower()
            if market not in ("kr", "us", "jp", "tw", "cn", "hk"):  # +intl(2026-06-13)
                market = "kr"
            from bot.earnings_calendar import render_page
            html = render_page(year, month, market)
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("earnings: failed — %s", exc)
            self.send_error(500, "internal error")

    def _handle_trade_proxy(self) -> None:
        """GET /trade[/...] — 한국 수출입(trade) 대시보드 리버스 프록시.

        같은 VM 의 trade 백엔드(기본 127.0.0.1:8765/dashboard)로 포워드 →
        우리 도메인·인증 아래 통합(외부 IP:port 노출 제거). 인증은 TRADE_
        PROXY_AUTH(user:pass) env, 없으면 브라우저가 보낸 Authorization 전달
        (같은 자격증명 가정). 백엔드 다운 시 502 graceful. 사용자 2026-06-10.

        ⚠️ trade 대시보드 경로/자산/인증 구조 미검증(trade repo 세션 미추가)
        — 절대경로 /dashboard/ → /trade/ rewrite. 깨지면 TRADE_PROXY_BASE/
        AUTH env 또는 rewrite 보정 필요(직접 IP 링크는 폴백으로 동작)."""
        import base64
        import os
        import urllib.error as _ue
        import urllib.request as _ur
        base = os.environ.get("TRADE_PROXY_BASE",
                              "http://127.0.0.1:8765/dashboard")
        sub = self.path[len("/trade"):]
        # trade 페이지의 상대링크(예: href="market.html")가 /trade/ 밑으로
        # 흡수되면 trade 백엔드가 자기 인덱스를 돌려줘 '홈 눌렀는데 수출입'
        # 오작동(사용자 2026-06-11). 우리 루트 페이지명이면 밖으로 redirect.
        _leaf = sub.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if _leaf in _OUR_ROOT_PAGES:
            _pfx = f"/{_TOKEN}" if _TOKEN else ""
            self.send_response(302)
            self.send_header("Location", f"{_pfx}/{_leaf}")
            self.end_headers()
            return
        if sub == "":
            sub = "/"
        if not sub.startswith(("/", "?")):
            sub = "/" + sub
        url = _trade_upstream_url(base, sub)
        headers = {"User-Agent": "NOAH-trade-proxy/1.0",
                   "Accept": self.headers.get("Accept", "*/*")}
        ta = os.environ.get("TRADE_PROXY_AUTH")
        if ta:
            headers["Authorization"] = "Basic " + base64.b64encode(
                ta.encode()).decode()
        elif self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]
        try:
            with _ur.urlopen(_ur.Request(url, headers=headers), timeout=25) as resp:
                body = resp.read()
                ctype = resp.headers.get("Content-Type", "text/html; charset=utf-8")
                status = resp.status
        except _ue.HTTPError as e:
            body = e.read() or b""
            ctype = e.headers.get("Content-Type", "text/html; charset=utf-8")
            status = e.code
        except Exception as exc:
            log.warning("trade proxy %s: %s", url, exc)
            self.send_error(502, "trade dashboard unavailable")
            return
        low = (ctype or "").lower()
        if any(t in low for t in ("html", "javascript", "css", "json")):
            body = _rewrite_trade_html(body, _TOKEN)
        # HTML 응답에 우리 nav 배너 주입 — 한국수출입 ↔ 홈·NOAH 연결(사용자
        # 2026-06-10 '메인보드·NOAH 분석대시보드 연결'). 토큰 prefix 포함
        # 절대경로라 trade 하위경로 무관. body 태그 직후 sticky 배너.
        if "html" in low:
            import re as _re
            _pfx = f"/{_TOKEN}" if _TOKEN else ""
            _banner = (
                '<div style="position:sticky;top:0;z-index:2147483647;'
                'background:#0d1117;color:#c9d1d9;padding:8px 14px;font-size:13px;'
                'border-bottom:1px solid #30363d;font-family:system-ui,-apple-system,sans-serif">'
                # onclick 강제 이동 — trade SPA 라우터가 클릭을 가로채
                # 자기 화면을 유지하던 것 차단(사용자 2026-06-11 '홈 눌러도
                # 수출입'). preventDefault 와 무관하게 location 직접 설정.
                f'<a href="{_pfx}/market.html" onclick="window.location.href=this.href;return false" style="color:#58a6ff;text-decoration:none;margin-right:16px">🌍 홈</a>'
                f'<a href="{_pfx}/index.html" onclick="window.location.href=this.href;return false" style="color:#58a6ff;text-decoration:none;margin-right:16px">🦉 종목분석</a>'
                '<span style="color:#8b949e">· 🌏 수출입</span></div>'
            ).encode("utf-8")
            m = _re.search(rb"<body[^>]*>", body, _re.IGNORECASE)
            if m:
                body = body[:m.end()] + _banner + body[m.end():]
            # <body> 없는 응답(industry_panel.html 등 lazy 프래그먼트, #455)엔 배너
            # 주입 안 함 — prepend 하면 탭 콘텐츠 안에 nav 가 중복 표시됨(사용자
            # 2026-06-16 '연결 대시보드 두번'). 풀페이지(<body> 보유)만 배너 1회.
        # 프록시 응답도 gzip — trade 페이지가 무압축으로 두 번(백엔드→프록시→브라우저)
        # 내려가던 것 해소(사용자 2026-06-28). 정적 서빙과 동일 기준.
        gz_ok = ("gzip" in self.headers.get("Accept-Encoding", "")
                 and any((ctype or "").lower().startswith(p)
                         for p in _GZIP_MIME_PREFIXES)
                 and len(body) >= _GZIP_MIN_BYTES)
        if gz_ok:
            body = gzip.compress(body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if gz_ok:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_naver_page(self, raw: str) -> None:
        """GET /theme | /highlow — 테마별 시세 · 신고가/신저가 페이지."""
        try:
            from bot.naver_pages import render_highlow_page, render_theme_page
            html = render_theme_page() if raw == "/theme" else render_highlow_page()
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("naver_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_simple_page(self, module: str, func: str) -> None:
        """GET → module.func() (인자 없는 렌더) → HTML. /nxt 등 단순 페이지 공용."""
        try:
            import importlib
            html = getattr(importlib.import_module(module), func)()
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("simple_page %s.%s: failed — %s", module, func, exc)
            self.send_error(500, "internal error")

    def _handle_us_page(self, raw: str) -> None:
        """GET /usindustry | /ushighlow | /usmovers | /usprepost — 미국 업종별
        시세 · 52주 신고가/신저가 · 급등급락 TOP30 · 장전/장후 (KR 미러)."""
        try:
            from bot.us_pages import (render_us_highlow_page,
                                      render_us_industry_page,
                                      render_us_movers_page,
                                      render_us_prepost_page)
            html = (render_us_industry_page() if raw == "/usindustry"
                    else render_us_movers_page() if raw == "/usmovers"
                    else render_us_prepost_page() if raw == "/usprepost"
                    else render_us_highlow_page())
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("us_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_tw_page(self, raw: str) -> None:
        """GET /twhighlow — 상한가·하한가 (TWSE) · /tw52 — 52주 신고가·신저가."""
        try:
            from bot.tw_pages import (render_tw_highlow_page,
                                      render_tw_highlow52_page)
            html = (render_tw_highlow52_page() if raw == "/tw52"
                    else render_tw_highlow_page())
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("tw_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_intl_page(self, raw: str) -> None:
        """GET /jp52 | /hk52 | /kr52 | /cn52 — JP/HK/KR/CN 52주 신고가·신저가."""
        try:
            from bot.intl_pages import render_intl_highlow52_page
            market = {"/jp52": "JP", "/hk52": "HK", "/kr52": "KR",
                      "/cn52": "CN_A"}[raw]
            encoded = render_intl_highlow52_page(market).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("intl_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_intl_movers(self, market: str) -> None:
        """GET /hkmovers — 홍콩 급등/급락 (무제한 시장, US 미러)."""
        try:
            from bot.intl_pages import render_intl_movers_page
            encoded = render_intl_movers_page(market).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("intl_movers %s: failed — %s", market, exc)
            self.send_error(500, "internal error")

    def _handle_jp_stop(self) -> None:
        """GET /jphighlow — 일본 상한가/하한가(ストップ高/安, TSE 制限値幅)."""
        try:
            from bot.intl_pages import render_jp_stop_page
            encoded = render_jp_stop_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("jp_stop page: failed — %s", exc)
            self.send_error(500, "internal error")

    def _handle_favorites_get(self) -> None:
        """GET /api/favorites — return saved favorites list with current prices."""
        try:
            from bot.market_favorites import get_favorites_with_prices
            self._json_ok({"ok": True, "favorites": get_favorites_with_prices()})
        except Exception as exc:
            log.warning("favorites_get: %s", exc)
            self._json_ok({"ok": False, "favorites": []})

    def _handle_favorite_add(self) -> None:
        """POST /api/favorite_add — save a ticker with current price snapshot."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            ticker = (payload.get("ticker") or "").strip()
            if not ticker or not _TICKER_RE.match(ticker):
                self._json_ok({"ok": False, "error": "invalid ticker"})
                return
            from bot.market_favorites import add_favorite
            entry = add_favorite(ticker)
            if entry is None:
                self._json_ok({"ok": False, "error": "duplicate or fetch failed"})
                return
            self._json_ok({"ok": True, "entry": entry})
        except Exception as exc:
            log.warning("favorite_add: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_important_get(self) -> None:
        """GET /api/important — 전 표면 중요 마크 + 메모 + 알람. 정적 페이지가 로드 시
        1회 받아 ★/📝/⏰ 상태·필터를 그린다(기기 무관 동기화)."""
        try:
            from bot.important_marks import all_marks
            out = {"ok": True, "marks": all_marks(), "memos": {}, "reminders": {}}
            try:
                from bot.memos import all_memos
                out["memos"] = all_memos()
            except Exception as exc:
                log.warning("important_get memos: %s", exc)
            try:
                from bot.reminders import all_reminders
                out["reminders"] = all_reminders()
            except Exception as exc:
                log.warning("important_get reminders: %s", exc)
            self._json_ok(out)
        except Exception as exc:
            log.warning("important_get: %s", exc)
            self._json_ok({"ok": False, "error": str(exc), "marks": {},
                           "memos": {}, "reminders": {}})

    def _handle_memo_post(self) -> None:
        """POST /api/memo {surface,id,text} — 카드 메모 저장(빈 텍스트=삭제)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 16384:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            from bot.memos import set_memo
            res = set_memo((payload.get("surface") or "").strip(),
                           (payload.get("id") or "").strip(),
                           payload.get("text") or "")
            self._json_ok(res)
        except Exception as exc:
            log.warning("memo_post: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    # pair → (marketindex 하위 패밀리, reutersCode). usdkrw 는 KRW-base
    # exchange(fetch_kr_fx), 나머지는 exchangeWorld cross-rate(fetch_world_fx,
    # 2026-07-14 유로·파운드·스위스프랑 확장) — reutersCode 표기가 그대로
    # "1 USD = N <통화>"(usdeur/usdgbp 는 반대로 "1 <통화> = N USD", EUR/GBP 는
    # Naver 도 forex 관례상 자국통화를 기준통화로 표기)라 FRED DEXJPUS/DEXCHUS/
    # DEXUSEU/DEXUSUK/DEXSZUS 단위와 그대로 일치(변환 불필요). 대만달러는 FRED
    # 미수록이라 히스토리 없이 추가했으나 사용자 요청으로 제외(2026-07-14).
    _FX_SOURCE = {"usdkrw": ("kr", "FX_USDKRW"),
                  "usdjpy": ("world", "USDJPY"),
                  "usdcny": ("world", "USDCNY"),
                  "usdeur": ("world", "EURUSD"),
                  "usdgbp": ("world", "GBPUSD"),
                  "usdchf": ("world", "USDCHF")}

    def _handle_fx_api(self, pair: str) -> None:
        """GET /api/usdkrw|usdjpy|usdcny|usdeur|usdgbp|usdchf →
        {rate, change, pct, src}. 네이버 marketindex(30초 캐시·graceful)
        재사용 — 실패/무데이터 시 {}(클라는 기존 값 유지)."""
        try:
            kind, code = self._FX_SOURCE[pair]
            if kind == "kr":
                from bot.naver_marketindex import fetch_kr_fx
                rec = (fetch_kr_fx() or {}).get(code) or {}
            else:
                from bot.naver_marketindex import fetch_world_fx
                rec = (fetch_world_fx() or {}).get(code) or {}
            if rec.get("close") is not None:
                self._json_ok({"rate": rec["close"],
                               "change": rec.get("change"),
                               "pct": rec.get("pct"), "src": "네이버 실시간"})
                return
        except Exception as exc:
            log.warning("fx_api(%s): %s", pair, exc)
        self._json_ok({})

    def _handle_vc_suppress_post(self) -> None:
        """POST /api/vc_suppress {id:"회사|관계|대상"} — 밸류체인 잘못된 관계 숨김(🗑️).
        영구 suppression 저장 + valuechain.html 재생성(다음 로드부터 제외). 멱등."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4096:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            edge_id = (payload.get("id") or "").strip()
            from bot.valuechain import add_suppressed
            ok = add_suppressed(edge_id)
            regen = False
            if ok:
                try:
                    from bot.dashboard import regenerate_valuechain_index
                    regenerate_valuechain_index()
                    regen = True
                except Exception as exc:
                    log.warning("vc_suppress: regen failed — %s", exc)
            # regen=false 면 저장은 됐으나 html 갱신 실패 → 다른 탭/리로드는 다음
            # 주기 regen 까지 stale(가시화, 리뷰 finding B · 실수노트 #11).
            self._json_ok({"ok": ok, "regen": regen})
        except Exception as exc:
            log.warning("vc_suppress_post: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_important_post(self) -> None:
        """POST /api/important {surface,id,on} — 카드 중요 토글. 반환 state=적용상태."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            surface = (payload.get("surface") or "").strip()
            mark_id = (payload.get("id") or "").strip()
            on = bool(payload.get("on"))
            from bot.important_marks import toggle
            state = toggle(surface, mark_id, on)
            if on and not state:        # 입력 거부(화이트리스트 밖·과대 id 등)
                self._json_ok({"ok": False, "error": "invalid surface/id"})
                return
            self._json_ok({"ok": True, "state": state})
        except Exception as exc:
            log.warning("important_post: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_reminder_post(self) -> None:
        """POST /api/reminder {surface,id,time,on,memo,card} — 알람 설정/해제."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 32768:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            from bot.reminders import set_reminder
            res = set_reminder(
                (payload.get("surface") or "").strip(),
                (payload.get("id") or "").strip(),
                (payload.get("time") or "").strip(),
                bool(payload.get("on")),
                memo=payload.get("memo") or "",
                card=payload.get("card") or "")
            self._json_ok(res)
        except Exception as exc:
            log.warning("reminder_post: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_favorite_remove(self) -> None:
        """POST /api/favorite_remove — remove a ticker from favorites."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            ticker = (payload.get("ticker") or "").strip()
            if not ticker:
                self._json_ok({"ok": False, "error": "missing ticker"})
                return
            from bot.market_favorites import remove_favorite
            removed = remove_favorite(ticker)
            self._json_ok({"ok": removed})
        except Exception as exc:
            log.warning("favorite_remove: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_favorite_reorder(self) -> None:
        """POST /api/favorite_reorder — move a ticker up/down in saved order."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("bad body")
            payload = json.loads(self.rfile.read(length))
            ticker = (payload.get("ticker") or "").strip()
            direction = (payload.get("direction") or "").strip()
            if not ticker:
                self._json_ok({"ok": False, "error": "missing ticker"})
                return
            if direction not in ("up", "down", "top", "bottom"):
                self._json_ok({"ok": False, "error": "invalid direction"})
                return
            from bot.market_favorites import reorder_favorite
            changed = reorder_favorite(ticker, direction)
            self._json_ok({"ok": changed})
        except Exception as exc:
            log.warning("favorite_reorder: %s", exc)
            self._json_ok({"ok": False, "error": str(exc)})

    def _handle_search_api(self) -> None:
        """GET /api/search?q=삼성전자 — resolve name → ticker JSON."""
        import urllib.parse as _ulp
        try:
            qs = _ulp.parse_qs(_ulp.urlparse(self.path).query)
            q = (qs.get("q", [""])[0] or "").strip()
            if not q:
                self._json_ok({"ticker": None, "error": "empty query"})
                return
            from bot.dashboard import resolve_name_to_ticker
            resolved = resolve_name_to_ticker(q)
            if resolved:
                self._json_ok({"ticker": resolved})
                return
            upper = q.upper()
            if _TICKER_RE.match(upper):
                self._json_ok({"ticker": upper})
            else:
                self._json_ok({"ticker": None, "error": "not found"})
        except Exception as exc:
            log.warning("search api: %s", exc)
            self._json_ok({"ticker": None, "error": str(exc)})

    def _json_ok(self, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_lookup(self) -> None:
        """Serve a stock overview page for any ticker or company name.
        GET /lookup/<TICKER_OR_NAME> — renders on-demand, 5min cache."""
        import time
        import urllib.parse as _ulp
        try:
            raw = self.path.split("?", 1)[0]
            raw_query = _ulp.unquote(raw.split("/lookup/", 1)[-1]).strip()
            try:
                from bot.dashboard import resolve_name_to_ticker
                resolved = resolve_name_to_ticker(raw_query)
                ticker = resolved if resolved else raw_query.upper()
            except Exception:
                ticker = raw_query.upper()
            if not ticker or not _TICKER_RE.match(ticker):
                self._serve_search_error(raw_query)
                return

            cache_dir = _ARCHIVE_ROOT.parent / "lookup_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = ticker.replace(".", "_").replace("-", "_")
            cache_f = cache_dir / f"{safe}.html"
            # 코드 배포 시 자동 무효화 — 캐시가 dashboard.py 보다 오래되면
            # 옛 마크업 서빙 금지(사용자 2026-06-11 stale lookup).
            try:
                import bot.dashboard as _dmod
                _code_mtime = os.path.getmtime(_dmod.__file__)
            except Exception:
                _code_mtime = 0.0
            if (cache_f.exists()
                    and (time.time() - cache_f.stat().st_mtime) < 300
                    and cache_f.stat().st_mtime > _code_mtime):
                try:
                    encoded = cache_f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, must-revalidate")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                except Exception:
                    pass

            from bot.dashboard import render_lookup_page
            html = render_lookup_page(ticker)
            encoded = html.encode("utf-8")
            try:
                cache_f.write_bytes(encoded)
            except Exception:
                pass
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("lookup: failed — %s", exc)
            self.send_error(500, "internal error")

    def _handle_run(self) -> None:
        """POST /api/run {kind, q} — 대시보드 '분석/실행' 버튼 → 봇 작업 스풀.

        kinds (bot.dashboard_requests.KINDS):
          • analyze  — q = 티커/종목명. 여기서 종목명→티커 resolve(api/search 와
            동일 경로) 후 resolved 티커를 spool. 실패 시 즉시 400(브라우저에
            바로 표시 — 봇까지 가서 조용히 죽지 않게).
          • screener — q = Bottleneck 도메인 ('' = 기본 bottleneck).
          • screen   — q = 조건부 스크리너 조건/프리셋.
        실행 자체는 텔레그램 봇 프로세스가 폴러로 집어 채널 명령과 동일
        경로로 수행 — 결과는 채널 + 아카이브에 게시. Basic Auth 뒤라 호출자
        는 사용자 본인; spool 단 dedupe + 8건 대기 cap 이 폭주 가드."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 1024:
                raise ValueError("missing or oversized request body")
            payload = json.loads(self.rfile.read(length))
            kind = (payload.get("kind") or "").strip().lower()
            q = " ".join((payload.get("q") or "").split())

            from bot.dashboard_requests import KINDS, submit
            if kind not in KINDS:
                raise ValueError(f"invalid kind: {kind!r}")

            if kind == "analyze":
                if not q:
                    raise ValueError("티커/종목명을 입력하세요")
                try:
                    from bot.dashboard import resolve_name_to_ticker
                    resolved = resolve_name_to_ticker(q)
                except Exception:
                    resolved = None
                ticker = (resolved or q).upper()
                if not _TICKER_RE.match(ticker):
                    raise ValueError(
                        f"'{q}' 종목을 찾지 못했습니다 — 티커(예: NVDA·005930.KS) "
                        "또는 정확한 종목명으로 다시 시도"
                    )
                q = ticker

            if kind == "command" and not q.startswith("/"):
                raise ValueError("명령은 '/' 로 시작해야 합니다")

            res = submit(kind, q)
            if not res.get("ok"):
                raise ValueError(res.get("error") or "요청 실패")
            log.info("run request spooled: kind=%s q=%r dup=%s",
                     kind, q, res.get("dup", False))
            self._reply_json(200, {"ok": True, "q": q,
                                   "id": res.get("id", ""),
                                   "dup": bool(res.get("dup")),
                                   "note": res.get("note", "")})
        except ValueError as exc:
            self._reply_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("run: unexpected failure")
            self._reply_json(500, {"ok": False, "error": str(exc)})

    def _handle_command_result(self) -> None:
        """GET /api/command_result?id=<hex> — 봇이 기록한 명령 결과 폴링.

        결과 있으면 {ok, done:true, lines:[...]}, 아직 실행 중이면
        {ok:true, done:false}. 봇 폴러(5초)+실행 시간만큼 지연될 수 있어
        브라우저가 주기 폴링한다. id 는 dashboard_requests 가 검증."""
        import urllib.parse as _uparse
        try:
            qs = _uparse.urlparse(self.path).query
            rid = (_uparse.parse_qs(qs).get("id", [""])[0] or "").strip()
            from bot.dashboard_requests import read_result
            res = read_result(rid)
            if res is None:
                self._reply_json(200, {"ok": True, "done": False})
                return
            self._reply_json(200, {"ok": bool(res.get("ok", True)),
                                   "done": bool(res.get("done")),
                                   "lines": res.get("lines", [])})
        except Exception as exc:
            self._reply_json(200, {"ok": False, "done": True,
                                   "error": str(exc)})

    def _handle_lookup_detail(self) -> None:
        """GET /api/lookup_detail?ticker=X — 지연로딩 detail HTML fragment.

        무거운 snapshot+enrichment+차트. **stale-while-revalidate** 디스크 캐시:
        - 신선(<30분): 즉시 서빙.
        - 만료~24h: stale 캐시 즉시 서빙 + 백그라운드 1회 재렌더(다음 방문 신선).
        - 콜드(캐시 없음): 동기 렌더 후 캐시·서빙(첫 1회만 ~10-30초).
        재렌더는 같은 render_lookup_detail 라 새 데이터-경로 코드 0. 첫 1회 외엔
        항상 즉시(사용자 2026-06-10 '아직도 느린데' — 재방문 즉시화). 슬로우-무빙
        filing/일간 데이터라 30분 신선창은 충분하고, 라이브 현재가/등락은 클라이언트
        _QUOTE_JS 가 별도 갱신하므로 캐시 신선도와 무관."""
        import time
        import urllib.parse as _ulp
        try:
            qs = _ulp.parse_qs(_ulp.urlparse(self.path).query)
            raw_q = (qs.get("ticker", [""])[0] or "").strip()
            try:
                from bot.dashboard import resolve_name_to_ticker
                resolved = resolve_name_to_ticker(raw_q)
                ticker = resolved if resolved else raw_q.upper()
            except Exception:
                ticker = raw_q.upper()
            if not ticker or not _TICKER_RE.match(ticker):
                self.send_error(400, "bad ticker")
                return

            # phase=core(스냅샷만, 빠름 — 앞쪽 탭) | full(enrichment 포함).
            phase = (qs.get("phase", ["full"])[0] or "full").strip().lower()
            enrich = phase != "core"

            cache_dir = _ARCHIVE_ROOT.parent / "lookup_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = ticker.replace(".", "_").replace("-", "_")
            # _v2: data-lk 파트 분할 fragment (구형식 캐시와 격리). core 는 별도 파일.
            _sfx = "_core" if not enrich else ""
            cache_f = cache_dir / f"detail_{safe}{_sfx}_v2.html"

            # 코드 배포 시 자동 무효화 — 캐시가 dashboard.py 보다 오래되면 옛
            # 마크업(사라진 탭·옛 정렬)을 최대 24h 서빙하게 됨. /lookup 핸들러엔
            # 이미 있던 가드를 SWR 경로에도 동일 적용(사용자 2026-08-16 분기실적
            # 정렬 미반영 — 코드는 맞는데 캐시가 옛 HTML 서빙).
            try:
                import bot.dashboard as _dmod
                _code_mtime = os.path.getmtime(_dmod.__file__)
            except Exception:
                _code_mtime = 0.0

            age = None
            if cache_f.exists():
                try:
                    _st = cache_f.stat()
                    age = time.time() - _st.st_mtime
                    if _st.st_mtime <= _code_mtime:
                        age = None  # 배포 이전 캐시 → 무효(동기 재렌더)
                except Exception:
                    age = None

            # 캐시 hit (신선 or stale-but-usable) → 즉시 서빙.
            if age is not None and age < _LOOKUP_DETAIL_STALE_SEC:
                encoded = None
                try:
                    encoded = cache_f.read_bytes()
                except Exception:
                    encoded = None
                if encoded:
                    # 만료(>30분)면 백그라운드 1회 재렌더 → 다음 방문은 신선.
                    if age >= _LOOKUP_DETAIL_FRESH_SEC:
                        _kick_lookup_detail_refresh(ticker, cache_f, enrich)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(encoded)
                    return

            # 콜드(캐시 없음/너무 오래됨/읽기 실패) → 동기 렌더.
            from bot.dashboard import render_lookup_detail
            _t_r = time.time()
            html = render_lookup_detail(ticker, enrich=enrich)
            _t_r = time.time() - _t_r
            # ⚠️ `/api/lookup_detail` 이 60~192초로 관측됐는데(2026-08-22
            # 실측) **어디서** 나는지 로그가 답하지 못했다 — 스냅샷 단계는
            # 이미 재고 있었는데 아무도 읽지 않았다(#43 아는 걸 화면·로그가
            # 말해야 한다). 캐시 히트로 단계가 비면 그 사실도 밝힌다(#54).
            try:
                import bot.stock_snapshot as _ss
                _st = _ss.last_timing(ticker)
                log.info("detail-timing %s phase=%s render=%.3fs %s", ticker,
                         phase, _t_r,
                         " ".join(f"{k}={v}s" for k, v in _st.items())
                         or "(스냅샷 캐시 히트 — 단계 없음)")
            except Exception:                                  # noqa: BLE001
                pass
            encoded = html.encode("utf-8")
            _atomic_write_bytes(cache_f, encoded)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("lookup_detail: failed — %s", exc)
            self.send_error(500, "internal error")

    def _serve_search_error(self, query: str) -> None:
        """Render a user-friendly 'not found' page for failed name search."""
        import html as _h
        q_esc = _h.escape(query)
        body = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>검색 결과 없음</title>'
            '<style>body{font-family:system-ui;background:#0d1117;color:#c9d1d9;'
            'display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}'
            '.box{text-align:center;max-width:400px;padding:40px}'
            'h2{font-size:20px;margin-bottom:12px}'
            'p{color:#8b949e;font-size:14px;line-height:1.6}'
            'a{color:#58a6ff;text-decoration:none}'
            'a:hover{text-decoration:underline}'
            '.q{color:#f0883e;font-weight:600}'
            '</style></head><body><div class="box">'
            f'<h2>검색 결과 없음</h2>'
            f'<p><span class="q">"{q_esc}"</span>에 해당하는 종목을 찾지 못했습니다.</p>'
            '<p>티커(NVDA, 005930.KS)를 직접 입력하거나<br>'
            '한국 종목명(삼성전자, LG에너지솔루션)을 정확히 입력해 주세요.</p>'
            '<p><a href="market.html">← 홈으로 돌아가기</a></p>'
            '</div></body></html>'
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_portfolio_send(self) -> None:
        """POST /api/portfolio_send body: {"to":"telegram"|"email",
        "csv":"<built-by-client>", "date":"YYYY-MM-DD"}.

        클라이언트가 렌더된 자산 표(손익변동·NOAH판정 포함)에서 만든 CSV 를
        받아 **본인에게만** 전송(텔레그램 DM / 이메일). 서버는 join 로직을
        재구현하지 않고 relay 만 → 보이는 그대로 전송. Basic-Auth gate 라
        인증된 소유자만 호출 가능. 5MB cap·대상 화이트리스트."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 5_000_000:
                raise ValueError("missing or oversized body")
            payload = json.loads(self.rfile.read(length))
            to = (payload.get("to") or "").strip()
            csv_text = payload.get("csv") or ""
            date = (payload.get("date") or "").strip()
            if to not in ("telegram", "email"):
                raise ValueError(f"invalid 'to': {to!r}")
            if not isinstance(csv_text, str) or not csv_text.strip():
                raise ValueError("empty csv")
            if len(csv_text) > 5_000_000:
                raise ValueError("oversized csv")
            kind = (payload.get("kind") or "").strip()
            is_budget = kind == "budget"
            prefix = "budget" if is_budget else "portfolio"
            fname = f"{prefix}_{date if _DATE_RE.match(date) else 'snapshot'}.csv"
            caption = "가계부" if is_budget else "자산"
            # UTF-8 BOM → Excel 이 한글을 정상 렌더.
            csv_bytes = ("\ufeff" + csv_text).encode("utf-8")
            from bot.portfolio_send import send as _pf_send
            ok, msg = _pf_send(csv_bytes, fname, to, caption=caption)
            self._reply_json(200 if ok else 502, {"ok": ok, "msg": msg})
        except Exception as exc:
            log.warning("portfolio_send: %s", exc)
            self._reply_json(400, {"ok": False, "msg": "요청 오류"})

    def _reply_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        level=logging.INFO,
    )
    parser = argparse.ArgumentParser(description="NOAH dashboard server")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()

    log.info("dotenv: %s", _DOTENV_STATUS)
    # lookup_cache 무효화 — 서버 재시작(=배포)마다 비운다. /lookup·/api/lookup_detail 의
    # 렌더 HTML(뉴스·리서치·동종비교 포함)은 SWR(30분 즉시서빙·24h) 디스크 캐시라,
    # 코드를 고쳐도 캐시된 옛 페이지가 계속 서빙돼 변경이 화면에 안 닿던 클래스(실수 #11
    # — 사용자 2026-06-19 ICHR 뉴스 원문링크 3회 재요청, #532 fix 가 stale 캐시에 가려짐).
    # 배포는 dashboard 재시작을 동반(auto-update bot/*.py diff)하므로 startup-clear 가
    # 배포→신선 렌더를 보장. 재시작 빈도라 1회 cold 렌더(SWR 재워밍)는 무해·graceful.
    try:
        _lc = _ARCHIVE_ROOT.parent / "lookup_cache"
        if _lc.is_dir():
            _cleared = 0
            for _f in _lc.glob("*.html"):
                try:
                    _f.unlink()
                    _cleared += 1
                except OSError:
                    pass
            log.info("lookup_cache 무효화: %d HTML 제거 (배포 후 신선 렌더 보장)", _cleared)
    except Exception as _cexc:
        log.debug("lookup_cache clear skipped: %s", _cexc)
    # 검색 워밍업 — api/search(종목명→ticker)는 DART corp_code 맵을 처음 1회
    # 로드할 때 느리다(서버 재시작 직후 첫 검색). 백그라운드로 미리 로드해
    # 첫 검색 '...' 지연 제거. 실패해도 서버 기동에 무영향.
    try:
        import threading as _warm_thr

        def _warm_search():
            try:
                from bot.dart_client import get_dart
                get_dart().find_by_name("삼성전자")  # forces corp_code map load
                log.info("search warmup: DART corp_code map loaded")
            except Exception as _wexc:
                log.debug("search warmup failed: %s", _wexc)

        _warm_thr.Thread(target=_warm_search, daemon=True).start()
    except Exception:
        pass
    server = ThreadingHTTPServer((args.bind, args.port), DashboardHandler)
    log.info(
        "serving %s on %s:%d  token=%s  basic_auth=%s",
        _ARCHIVE_ROOT,
        args.bind,
        args.port,
        "on" if _TOKEN else "off",
        "on" if (_AUTH_USER and _AUTH_PASSWORD) else "off",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("shutdown requested")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
