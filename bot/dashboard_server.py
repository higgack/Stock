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

_TOKEN = (os.getenv("DASHBOARD_TOKEN") or "").strip()
_AUTH_USER = (os.getenv("DASHBOARD_USER") or "").strip()
_AUTH_PASSWORD = (os.getenv("DASHBOARD_PASSWORD") or "").strip()
_AUTH_REALM = "NOAH stock dashboard"


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
        if path_lower.endswith((".html", "/")) or path_lower == "":
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
        # /lookup/<TICKER> — lightweight stock overview page (on-demand).
        raw = self.path.split("?", 1)[0]
        if raw.startswith("/lookup/"):
            return self._handle_lookup()
        if raw.startswith("/api/search"):
            return self._handle_search_api()
        return super().do_GET()

    def do_HEAD(self):
        if not self._authorize():
            return
        return super().do_HEAD()

    def do_POST(self):
        if not self._authorize():
            return
        if self.path == "/api/screener_delete":
            return self._handle_screener_delete()
        if self.path == "/api/daily_byte_delete":
            return self._handle_daily_byte_delete()
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

    def _handle_search_api(self) -> None:
        """GET /api/search?q=삼성전자 — resolve name → ticker JSON."""
        import urllib.parse as _ulp
        try:
            qs = _ulp.parse_qs(_ulp.urlparse(self.path).query)
            q = (qs.get("q", [""])[0] or "").strip()
            if not q:
                self._json_ok({"ticker": None, "error": "empty query"})
                return
            upper = q.upper()
            if _TICKER_RE.match(upper):
                self._json_ok({"ticker": upper})
                return
            from bot.dashboard import resolve_name_to_ticker
            resolved = resolve_name_to_ticker(q)
            if resolved:
                self._json_ok({"ticker": resolved})
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
            ticker = raw_query.upper()
            if not ticker or not _TICKER_RE.match(ticker):
                try:
                    from bot.dashboard import resolve_name_to_ticker
                    resolved = resolve_name_to_ticker(raw_query)
                    if resolved:
                        ticker = resolved
                    else:
                        self._serve_search_error(raw_query)
                        return
                except Exception:
                    self._serve_search_error(raw_query)
                    return

            cache_dir = _ARCHIVE_ROOT.parent / "lookup_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = ticker.replace(".", "_").replace("-", "_")
            cache_f = cache_dir / f"{safe}.html"
            if cache_f.exists() and (time.time() - cache_f.stat().st_mtime) < 300:
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
