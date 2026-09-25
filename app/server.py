"""DSA Review - a local web app for spaced-repetition practice of coding problems.

Run:  python app/server.py            (opens your browser)
      python app/server.py --no-browser --port 8765 --data-dir data

The server only listens on 127.0.0.1 (your own computer). It uses nothing but the
Python standard library plus the `fsrs` package.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

import claude_help  # noqa: E402
import neetcode  # noqa: E402
import runner  # noqa: E402
import scheduling as sched  # noqa: E402
from store import Invalid, NotFound, Store, _as_float, _as_int  # noqa: E402

STATIC_DIR = APP_DIR / "static"
MAX_BODY = 20 * 1024 * 1024
# Explicit types: on Windows, mimetypes reads the registry, which some installers
# break (e.g. .js as text/plain), and the browser would then refuse to run the app.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")


WINDOWS = sys.platform == "win32"


class LocalServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that won't share a port with another program.

    http.server turns on SO_REUSEADDR. On Windows that option lets a second socket
    bind a port that is already listening, so a second copy of the app (run.bat
    double-clicked twice) would quietly share port 8765 with the first one instead
    of moving on to 8766. Windows doesn't need the option for quick restarts, so it
    is simply left off there (elsewhere it only skips the TIME_WAIT delay).
    """

    def server_bind(self):
        if WINDOWS:
            self.allow_reuse_address = False
        super().server_bind()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "DSAReview/1.0"
    store: Store = None  # set in main()
    port: int = 0
    lock = threading.Lock()  # serialize writes; SQLite is happiest that way

    # ---------------------------------------------------------------- helpers
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _security_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload, extra: dict | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _check_host(self):
        # Blocks DNS-rebinding: only accept requests addressed to this machine.
        host = (self.headers.get("Host") or "").lower()
        allowed = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        if host not in allowed:
            raise ApiError(HTTPStatus.FORBIDDEN, "bad Host header")

    def _check_write_allowed(self):
        # Blocks other websites from posting to this app (CSRF): browsers can't send
        # a cross-site JSON request without a CORS preflight, which we never approve.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
        origin = self.headers.get("Origin")
        if origin and origin.lower() not in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            raise ApiError(HTTPStatus.FORBIDDEN, "cross-origin request refused")

    def _body(self) -> dict:
        raw_len = (self.headers.get("Content-Length") or "0").strip()
        if not raw_len.isdigit():
            self.close_connection = True
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        length = int(raw_len)
        if length > MAX_BODY:
            self.close_connection = True
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "body is not valid JSON")
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
        return data

    def _dispatch(self):
        try:
            try:
                self._check_host()
                url = urlparse(self.path)
                if url.path.startswith("/api/"):
                    if self.command not in ("GET", "HEAD"):
                        self._check_write_allowed()
                    self._api(url)
                elif self.command in ("GET", "HEAD"):
                    self._static(url.path)
                else:
                    raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            except ApiError as e:
                self._json(e.status, {"error": e.message})
            except Invalid as e:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(e)})
            except NotFound as e:
                self._json(HTTPStatus.NOT_FOUND, {"error": str(e)})
            except claude_help.ClaudeError as e:
                self._json(e.status, {"error": e.message})
            except (ConnectionError, TimeoutError):
                raise
            except Exception:  # last resort: details go to the terminal window, not the browser
                traceback.print_exc()
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": "unexpected server error (details are in the app's terminal window)"})
        except (ConnectionError, TimeoutError):
            self.close_connection = True  # the browser went away mid-response; nothing to do

    do_GET = do_HEAD = do_POST = do_PATCH = do_PUT = do_DELETE = _dispatch

    # ---------------------------------------------------------------- static files
    def _static(self, path: str):
        if path in ("/", "/index.html"):
            target = STATIC_DIR / "index.html"
        elif path.startswith("/static/"):
            target = (STATIC_DIR / path[len("/static/"):]).resolve()
            if STATIC_DIR not in target.parents:
                raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        elif path == "/favicon.ico":
            target = STATIC_DIR / "favicon.svg"
        else:
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if ctype is None or not target.is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    # ---------------------------------------------------------------- API
    def _api(self, url):
        path, method = url.path.rstrip("/"), self.command
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        store = self.store

        if path == "/api/summary" and method == "GET":
            return self._json(200, store.summary(scope=query.get("deck", "")))

        if path == "/api/queue" and method == "GET":
            return self._json(200, store.queue(tag=query.get("tag", ""), scope=query.get("deck", "")))

        if path == "/api/tags" and method == "GET":
            return self._json(200, {"tags": store.tags(scope=query.get("deck", ""))})

        if path == "/api/neetcode" and method == "GET":
            return self._json(200, neetcode.build_tracker(store.list_problems(scope="all")))

        if path == "/api/neetcode/adopt" and method == "POST":
            data = self._body()
            ids = data.get("problem_ids")
            if ids is not None and not isinstance(ids, list):
                raise ApiError(HTTPStatus.BAD_REQUEST, "problem_ids must be a list")
            with self.lock:
                return self._json(200, neetcode.adopt(store, ids))

        if path == "/api/problems":
            if method == "GET":
                items = store.list_problems(q=query.get("q", ""), tag=query.get("tag", ""),
                                            status=query.get("status", ""), scope=query.get("deck", ""))
                return self._json(200, {"problems": items})
            if method == "POST":
                data = self._body()
                first_rating = data.pop("first_rating", None)
                duration_ms = data.pop("duration_ms", None)
                if first_rating == "":
                    first_rating = None
                with self.lock:
                    problem = store.create_problem(data, first_rating, duration_ms)
                return self._json(201, problem)

        m = re.fullmatch(r"/api/problems/(\d+)(?:/(review|undo|draft))?", path)
        if m:
            pid, action = int(m.group(1)), m.group(2)
            if action is None and method == "GET":
                return self._json(200, store.get_problem(pid))
            if action is None and method in ("PATCH", "PUT"):
                data = self._body()
                with self.lock:
                    return self._json(200, store.update_problem(pid, data))
            if action is None and method == "DELETE":
                with self.lock:
                    store.delete_problem(pid)
                return self._json(200, {"deleted": pid})
            if action == "review" and method == "POST":
                data = self._body()
                with self.lock:
                    problem = store.review_problem(pid, data.get("rating"), data.get("duration_ms"))
                return self._json(200, problem)
            if action == "undo" and method == "POST":
                data = self._body()
                with self.lock:
                    return self._json(200, store.undo_last_review(pid, data.get("review_id")))
            if action == "draft" and method == "GET":
                return self._json(200, store.get_draft(pid))
            # POST too: navigator.sendBeacon (used to save a draft when the tab closes) can only POST.
            if action == "draft" and method in ("PUT", "PATCH", "POST"):
                data = self._body()
                if "code" not in data:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "code is required")
                with self.lock:
                    return self._json(200, store.save_draft(pid, data.get("code"), data.get("language")))

        if path == "/api/settings":
            if method == "GET":
                settings = store.get_settings()
            elif method in ("PATCH", "PUT"):
                with self.lock:
                    settings = store.update_settings(self._body())
            else:
                raise ApiError(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            return self._json(200, {
                "settings": settings,
                "interval_preview": sched.interval_preview(store.scheduler_settings(settings)),
            })

        if path == "/api/settings/preview" and method == "GET":
            s = store.get_settings()
            if "desired_retention" in query:
                s["desired_retention"] = _as_float(query["desired_retention"], "desired_retention", 0.70, 0.99)
            if "maximum_interval" in query:
                s["maximum_interval"] = _as_int(query["maximum_interval"], "maximum_interval", 1, 36500)
            return self._json(200, {"interval_preview": sched.interval_preview(store.scheduler_settings(s))})

        if path == "/api/export" and method == "GET":
            payload = store.export_all()
            stamp = payload["exported_at"][:10]
            return self._json(200, payload, {
                "Content-Disposition": f'attachment; filename="dsa-review-backup-{stamp}.json"'})

        if path == "/api/import" and method == "POST":
            data = self._body()
            with self.lock:
                return self._json(200, store.import_all(data))

        if path == "/api/run" and method == "POST":
            if not store.get_settings().get("allow_code_run", True):
                raise ApiError(HTTPStatus.FORBIDDEN,
                                "Running code is turned off. Turn it back on in Settings to use Run.")
            data = self._body()
            code = data.get("code")
            if not isinstance(code, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "code must be text")
            if len(code) > 100000:
                raise ApiError(HTTPStatus.BAD_REQUEST, "code is too long (max 100000 characters)")
            stdin = data.get("stdin") or ""
            if not isinstance(stdin, str):
                raise ApiError(HTTPStatus.BAD_REQUEST, "stdin must be text")
            # Deliberately outside self.lock: a run can take up to ~10s and doesn't
            # touch the database, so it must never block other requests. runner's own
            # lock (one run at a time) is what actually serializes this endpoint.
            try:
                result = runner.run_python(code, stdin)
            except runner.RunInProgress:
                raise ApiError(HTTPStatus.CONFLICT, "Another run is already in progress. Wait for it to finish.")
            return self._json(200, result)

        # ---- Ask Claude (app/claude_help.py). Deliberately outside self.lock: these
        # do network I/O / spawn a subprocess and never touch the SQLite database.
        if path == "/api/claude/status" and method == "GET":
            return self._json(200, claude_help.status(store))

        if path == "/api/claude/key":
            if method == "PUT":
                data = self._body()
                claude_help.save_api_key(store, data.get("api_key"))
                return self._json(200, {"ok": True})
            if method == "DELETE":
                self._body()
                claude_help.delete_api_key(store)
                return self._json(200, {"ok": True})

        if path == "/api/claude/test" and method == "POST":
            self._body()
            return self._json(200, claude_help.test_connection(store))

        if path == "/api/claude/help" and method == "POST":
            data = self._body()
            return self._json(200, claude_help.handle_help(store, data))

        raise ApiError(HTTPStatus.NOT_FOUND, f"no API route for {method} {path}")


def main(argv=None):
    root = APP_DIR.parent
    parser = argparse.ArgumentParser(description="DSA Review - spaced repetition for coding problems")
    parser.add_argument("--port", type=int, default=8765, help="port to try first (default 8765)")
    parser.add_argument("--data-dir", default=str(root / "data"), help="where the database lives")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    store = Store(data_dir / "dsa_review.db")
    store.backup(data_dir / "backups")

    httpd = None
    for port in range(args.port, args.port + 20):
        try:
            httpd = LocalServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not find a free port between {args.port} and {args.port + 19}.")
        return 1

    Handler.store = store
    Handler.port = port
    url = f"http://127.0.0.1:{port}/"
    print("DSA Review is running.")
    print(f"  Open:     {url}")
    print(f"  Data:     {data_dir / 'dsa_review.db'}")
    print("  Stop:     press Ctrl+C in this window (or just close it)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
