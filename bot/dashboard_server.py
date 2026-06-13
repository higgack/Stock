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
import json
import logging
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
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
    "market.html", "index.html", "screener.html", "screener_domains.html",
    "dart_feed.html", "watchlist.html", "daily_byte.html", "portfolio.html",
    "budget.html", "paper.html", "reddit_insider.html", "realestate.html",
    "cheongyak.html", "gics_candidates.html", "errors.html",
))

_TOKEN = (os.getenv("DASHBOARD_TOKEN") or "").strip()
_AUTH_USER = (os.getenv("DASHBOARD_USER") or "").strip()
_AUTH_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "").strip()
_AUTH_REALM = "NOAH stock dashboard"

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
            from bot.dashboard import render_lookup_detail
            html = render_lookup_detail(ticker, enrich=enrich)
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
        if (path_lower.endswith((".html", "/")) or path_lower == ""
                or path_lower in ("/earnings", "/theme", "/highlow",
                                  "/usindustry", "/ushighlow", "/usmovers",
                                  "/twhighlow", "/tw52",
                                  "/jp52", "/cn52", "/hk52", "/kr52",
                                  "/hkmovers", "/jphighlow")
                or path_lower.startswith("/lookup/")
                or path_lower == "/trade" or path_lower.startswith("/trade/")):
            # /trade* — 프록시는 매 요청 trade 백엔드로 fresh fetch(서버 캐시
            # 0)지만, no-cache 가 없으면 브라우저가 옛 HTML 을 캐시해 갱신이
            # 안 보임 → 항상 revalidate (사용자 2026-06-11 '업데이트 실시간?').
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        super().end_headers()

    # ── Request handlers ─────────────────────────────────────────────
    def do_GET(self):
        if not self._authorize():
            return
        # /api/chart?ticker=..&interval=1d|1wk|1mo&range=1mo|3mo|6mo|ytd|1y|3y|5y|max
        # On-demand timeframe fetch for the detail-page price chart. The
        # token prefix is already stripped by _authorize() above.
        if self.path.split("?", 1)[0] == "/api/chart":
            return self._handle_chart_api()
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
        # /usindustry · /ushighlow · /usmovers — 미국 업종별 시세 · 52주
        # 신고가/신저가 · 급등급락 TOP30 (KR 미러 — 사용자 2026-06-10/12)
        if raw in ("/usindustry", "/ushighlow", "/usmovers"):
            return self._handle_us_page(raw)
        # /twhighlow — 대만 상한가/하한가 (TWSE) · /tw52 — 52주 신고가/신저가
        # (yfinance 유니버스 백그라운드, 사용자 2026-06-13 Phase 2)
        if raw in ("/twhighlow", "/tw52"):
            return self._handle_tw_page(raw)
        # /jp52 /cn52 /hk52 /kr52 — JP/CN/HK/KR 52주 신고가/신저가 (유니버스 백그라운드)
        if raw in ("/jp52", "/cn52", "/hk52", "/kr52"):
            return self._handle_intl_page(raw)
        # /hkmovers — 홍콩 급등/급락 (무제한 시장, US 미러, 사용자 2026-06-13)
        if raw == "/hkmovers":
            return self._handle_intl_movers("HK")
        # /jphighlow — 일본 상한가/하한가 (TSE 制限値幅, 사용자 2026-06-13)
        if raw == "/jphighlow":
            return self._handle_jp_stop()
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
        return super().do_GET()

    def do_HEAD(self):
        if not self._authorize():
            return
        return super().do_HEAD()

    def do_POST(self):
        if not self._authorize():
            return
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
        if self.path == "/api/realestate_delete":
            return self._handle_simple_delete(
                "realestate_archive", r"^\d{6}_[a-zA-Z0-9_]{1,40}\.json$",
                "regenerate_realestate_index")
        if self.path == "/api/cheongyak_delete":
            return self._handle_simple_delete(
                "cheongyak_archive", r"^\d{6}_[a-zA-Z0-9_]{1,40}\.json$",
                "regenerate_cheongyak_index")
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
                              regen_fn: str) -> None:
        """Generic per-run JSON archive delete under ~/.tradingagents/<subdir>/
        YYYY-MM-DD/<filename>. Validates date + filename (path-traversal
        guard) then unlinks + calls bot.dashboard.<regen_fn>(). Used by
        /api/realestate_delete (and future archive surfaces)."""
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
                getattr(_dash, regen_fn)()
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

    def _handle_chart_api(self) -> None:
        """Serve an on-demand chart payload for a ticker / interval / range.
        Validates inputs, 1h disk-caches per (ticker, interval, range), and
        fetches via yfinance (free). Read-only GET — gated by _authorize()."""
        import time
        import urllib.parse as _uparse

        _VALID_INTERVALS = {"5m", "15m", "1h", "1d", "1wk", "1mo"}
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
            cache_f = cache_dir / f"{safe}_{interval}_{rng}_v4.json"
            # TTL 5 min — last_price 가 장중 갱신되도록. yfinance 호출은
            # 종목당 5분당 1회 → 단일 채널 audience 면 무료한도 안전 (~2000/h).
            if cache_f.exists() and (time.time() - cache_f.stat().st_mtime) < 300:
                try:
                    self._reply_json(200, json.loads(cache_f.read_text("utf-8")))
                    return
                except Exception:
                    pass  # corrupt cache → refetch

            from bot.chart_data import fetch_chart_payload
            payload = fetch_chart_payload(ticker, interval=interval, period=rng)
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
            cache_f = cache_dir / f"{safe}_{kind}_v5.json"
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
            quote = build_live_quote(ticker, full=full)
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
            if market not in ("kr", "us"):
                market = "kr"
            from bot.earnings_calendar import render_page
            html = render_page(year, month, market)
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        url = base.rstrip("/") + sub
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
            body = body.replace(b"/dashboard/", b"/trade/")
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
                f'<a href="{_pfx}/index.html" onclick="window.location.href=this.href;return false" style="color:#58a6ff;text-decoration:none;margin-right:16px">🦉 NOAH 종목분석</a>'
                '<span style="color:#8b949e">· 🇰🇷 한국 수출입</span></div>'
            ).encode("utf-8")
            m = _re.search(rb"<body[^>]*>", body, _re.IGNORECASE)
            if m:
                body = body[:m.end()] + _banner + body[m.end():]
            else:
                body = _banner + body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
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
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("naver_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_us_page(self, raw: str) -> None:
        """GET /usindustry | /ushighlow | /usmovers — 미국 업종별 시세 ·
        52주 신고가/신저가 · 급등급락 TOP30 (KR /theme·/highlow 미러)."""
        try:
            from bot.us_pages import (render_us_highlow_page,
                                      render_us_industry_page,
                                      render_us_movers_page)
            html = (render_us_industry_page() if raw == "/usindustry"
                    else render_us_movers_page() if raw == "/usmovers"
                    else render_us_highlow_page())
            encoded = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            log.warning("tw_page %s: failed — %s", raw, exc)
            self.send_error(500, "internal error")

    def _handle_intl_page(self, raw: str) -> None:
        """GET /jp52 | /cn52 | /hk52 | /kr52 — JP/CN/HK/KR 52주 신고가·신저가."""
        try:
            from bot.intl_pages import render_intl_highlow52_page
            market = {"/jp52": "JP", "/cn52": "CN_A", "/hk52": "HK",
                      "/kr52": "KR"}[raw]
            encoded = render_intl_highlow52_page(market).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
            if direction not in ("up", "down"):
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
            # _v2: data-lk 파트 분할 fragment (구형식 캐시와 격리 — 사용자
            # 2026-06-11 lookup 레이아웃 재구성). core 는 별도 파일.
            _sfx = "_core" if not enrich else ""
            cache_f = cache_dir / f"detail_{safe}{_sfx}_v2.html"

            age = None
            if cache_f.exists():
                try:
                    age = time.time() - cache_f.stat().st_mtime
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
            html = render_lookup_detail(ticker, enrich=enrich)
            encoded = html.encode("utf-8")
            _atomic_write_bytes(cache_f, encoded)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-cache")
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
