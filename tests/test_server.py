"""Tests for app/server.py: the JSON API, CSRF / DNS-rebinding guards, static files."""
import contextlib
import http.client
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import StoreTestCase, count_rows  # noqa: E402  (puts app/ on sys.path)

import server  # noqa: E402
from store import Store  # noqa: E402

JSON_CT = "application/json; charset=utf-8"
_NO_BODY = object()


def quiet_server_errors():
    """The server prints tracebacks for unexpected errors; keep test output clean."""
    return contextlib.redirect_stderr(io.StringIO())


class Response:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self):
        return f"<Response {self.status} {self.body[:200]!r}>"


class ServerTestCase(StoreTestCase):
    """One live server per test class (random port); a fresh Store for every test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A subclass so the real Handler's class attributes are never touched.
        cls.handler = type(f"{cls.__name__}Handler", (server.Handler,), {"store": None})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), cls.handler)
        cls.httpd.daemon_threads = False  # so server_close() joins every request thread
        cls.port = cls.handler.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls._stop_server)

    @classmethod
    def _stop_server(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(10)

    def setUp(self):
        super().setUp()
        self.handler.store = self.store
        self.addCleanup(setattr, self.handler, "store", None)

    # ------------------------------------------------------------------ client helpers
    def request(self, method, path, body=_NO_BODY, *, raw=None, headers=None,
                content_type="application/json"):
        """Send one HTTP request and parse the reply with http.client.

        The whole request (headers + body) goes out in a single sendall(). http.client's
        own HTTPConnection sends the body separately; when the server rejects a request
        before reading its body (403/404/413/415) and closes the socket, the unread bytes
        can turn the close into a TCP reset and the client may lose the response.
        ``headers`` override the defaults; a value of None removes that header.
        """
        data = raw if raw is not None else (b"" if body is _NO_BODY else
                                            json.dumps(body).encode("utf-8"))
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        if content_type is not None and method not in ("GET", "HEAD"):
            hdrs["Content-Type"] = content_type
        if data or method in ("POST", "PUT", "PATCH"):
            hdrs["Content-Length"] = str(len(data))
        for key, value in (headers or {}).items():
            for existing in [k for k in hdrs if k.lower() == key.lower()]:
                del hdrs[existing]
            if value is not None:
                hdrs[key] = value
        head = "\r\n".join([f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in hdrs.items()])
        wire = (head + "\r\n\r\n").encode("latin-1") + data
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(wire)
            resp = http.client.HTTPResponse(sock, method=method)
            try:
                resp.begin()
                payload = resp.read()
            finally:
                resp.close()
        return Response(resp.status, resp.headers, payload)

    def raw_request(self, data: bytes, timeout=5.0) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            s.sendall(data)
            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)

    def call(self, method, path, body=_NO_BODY, expect=200, **kw):
        resp = self.request(method, path, body, **kw)
        self.assertEqual(resp.status, expect, resp)
        self.assertEqual(resp.headers["Content-Type"], JSON_CT)
        return resp.json()

    def assertError(self, resp, status, fragment=None):
        self.assertEqual(resp.status, status, resp)
        self.assertEqual(resp.headers["Content-Type"], JSON_CT)
        data = resp.json()
        self.assertEqual(set(data), {"error"})
        self.assertIsInstance(data["error"], str)
        if fragment:
            self.assertIn(fragment, data["error"])
        self.assertSecurityHeaders(resp)

    def assertSecurityHeaders(self, resp):
        csp = resp.headers["Content-Security-Policy"]
        self.assertIsNotNone(csp)
        for directive in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'",
                          "base-uri 'none'", "form-action 'self'", "connect-src 'self'"):
            self.assertIn(directive, csp)
        script_src = re.search(r"script-src([^;]*)", csp).group(1)
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", csp)
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
        self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertIsNone(resp.headers["Access-Control-Allow-Origin"])

    def create(self, **fields):
        fields.setdefault("title", "Two Sum")
        return self.call("POST", "/api/problems", fields, expect=201)


# ============================================================================ happy paths
class ProblemEndpointsTest(ServerTestCase):
    def test_create_new_problem(self):
        resp = self.request("POST", "/api/problems", {
            "title": "Two Sum", "url": "https://leetcode.com/problems/two-sum/",
            "difficulty": "easy", "tags": "Hash Map, Arrays", "first_rating": None})
        self.assertEqual(resp.status, 201)
        self.assertSecurityHeaders(resp)
        p = resp.json()
        self.assertEqual((p["title"], p["difficulty"], p["tags"], p["status"]),
                         ("Two Sum", "Easy", ["hash-map", "arrays"], "new"))
        self.assertEqual(p["history"], [])
        self.assertEqual(set(p["preview"]), {"again", "hard", "good", "easy"})
        self.assertEqual(p["preview"]["good"]["interval_days"], 2.0)
        self.assertEqual(self.store.get_problem(p["id"])["uid"], p["uid"])

    def test_create_with_first_rating(self):
        p = self.create(title="Solved", first_rating=3, duration_ms=60000)
        self.assertEqual((p["status"], p["reps"], p["last_rating"]), ("scheduled", 1, 3))
        self.assertEqual(p["history"][0]["duration_ms"], 60000)
        self.assertEqual(p["history"][0]["rating_name"], "good")
        self.assertEqual((p["due_in_days"], p["overdue_days"]), (2, 0))
        self.assertEqual(p["history"][0]["interval_days"], 2)
        self.assertEqual(p["history"][0]["kind"], "added")
        # `due` is when the due study date starts (4am local); `due_date` is that date
        due_d = date.fromisoformat(p["due_date"])
        self.assertEqual(datetime.fromisoformat(p["due"]),
                         datetime(due_d.year, due_d.month, due_d.day, 4).astimezone())
        self.assertEqual(p["history"][0]["next_due"], p["due"])

    def test_first_rating_blank_values_mean_new(self):
        for value in ("", 0, None, False):
            with self.subTest(value=value):
                p = self.create(first_rating=value)
                self.assertEqual(p["status"], "new")
        p = self.create()  # omitted
        self.assertEqual(p["status"], "new")
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    def test_first_rating_numeric_string(self):
        self.assertEqual(self.create(first_rating="4")["last_rating"], 4)

    def test_unicode_round_trip(self):
        title = "두 수의 합 — Two Sum 🚀"
        p = self.create(title=title, notes="メモ\n")
        got = self.call("GET", f"/api/problems/{p['id']}")
        self.assertEqual((got["title"], got["notes"]), (title, "メモ\n"))

    def test_get_problem(self):
        pid = self.create(first_rating=2)["id"]
        p = self.call("GET", f"/api/problems/{pid}")
        self.assertEqual(p["id"], pid)
        self.assertEqual(len(p["history"]), 1)
        self.assertEqual(set(p["history"][0]),
                         {"id", "rating", "rating_name", "reviewed_at", "duration_ms",
                          "next_due", "interval_days", "stability", "kind"})
        for name in ("again", "hard", "good", "easy"):
            self.assertEqual(set(p["preview"][name]),
                             {"rating", "due", "due_date", "interval_days", "stability",
                              "difficulty"})
            self.assertIsInstance(p["preview"][name]["interval_days"], int)
        for key in ("due", "due_date", "due_in_days", "overdue_days", "last_review"):
            self.assertIsNotNone(p[key], key)
        direct = self.store.get_problem(pid)
        for key in ("uid", "title", "due", "last_review", "stability", "reps", "history"):
            self.assertEqual(p[key], direct[key], key)

    def test_list_problems_and_filters(self):
        a = self.create(title="Two Sum", tags=["arrays"])["id"]
        b = self.create(title="Course Schedule", tags=["graphs"], first_rating=3)["id"]
        c = self.create(title="Word Ladder", tags=["graphs"], first_rating=4)["id"]

        def ids(query=""):
            data = self.call("GET", "/api/problems" + query)
            self.assertEqual(set(data), {"problems"})
            for p in data["problems"]:
                self.assertNotIn("history", p)
            return [p["id"] for p in data["problems"]]

        self.assertEqual(ids(), [a, b, c])
        self.assertEqual(ids("?tag=graphs"), [b, c])
        self.assertEqual(ids("?status=new"), [a])
        self.assertEqual(ids("?q=WORD"), [c])
        self.assertEqual(ids("?q=course&tag=graphs&status=scheduled"), [b])
        self.assertEqual(ids("?q=&tag=&status="), [a, b, c])
        self.assertEqual(ids("?q=%EB%91%90"), [])  # percent-encoded unicode query
        self.assertEqual(ids("?tag=graphs&tag=arrays"), [a])  # last value wins
        self.assertEqual(ids("/"), [a, b, c])  # trailing slash

    def test_patch_problem(self):
        pid = self.create(notes="old")["id"]
        p = self.call("PATCH", f"/api/problems/{pid}", {"title": "Renamed", "tags": ["X Y"]})
        self.assertEqual((p["title"], p["tags"], p["notes"]), ("Renamed", ["x-y"], "old"))
        self.assertIn("history", p)
        p = self.call("PUT", f"/api/problems/{pid}", {"notes": "via put"})
        self.assertEqual((p["title"], p["notes"]), ("Renamed", "via put"))
        p = self.call("PATCH", f"/api/problems/{pid}", {"suspended": True})
        self.assertEqual(p["status"], "suspended")
        p = self.call("PATCH", f"/api/problems/{pid}", {})
        self.assertEqual(p["title"], "Renamed")

    def test_delete_problem(self):
        pid = self.create(first_rating=3)["id"]
        self.assertEqual(self.call("DELETE", f"/api/problems/{pid}", {}), {"deleted": pid})
        self.assertError(self.request("GET", f"/api/problems/{pid}"), 404, "not found")
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    def test_delete_without_body(self):
        pid = self.create()["id"]
        self.assertEqual(self.call("DELETE", f"/api/problems/{pid}"), {"deleted": pid})

    def test_review_and_undo(self):
        pid = self.create()["id"]
        p = self.call("POST", f"/api/problems/{pid}/review", {"rating": 3, "duration_ms": 1500})
        self.assertEqual((p["status"], p["reps"], p["last_rating"]), ("scheduled", 1, 3))
        self.assertEqual(p["history"][0]["duration_ms"], 1500)
        p = self.call("POST", f"/api/problems/{pid}/review", {"rating": "1"})
        self.assertEqual((p["reps"], p["lapses"], p["last_rating"]), (2, 1, 1))
        p = self.call("POST", f"/api/problems/{pid}/undo", {})
        self.assertEqual((p["reps"], p["last_rating"]), (1, 3))
        p = self.call("POST", f"/api/problems/{pid}/undo")  # no body at all
        self.assertEqual(p["status"], "new")
        self.assertError(self.request("POST", f"/api/problems/{pid}/undo", {}), 400,
                         "no reviews")

    # Regression test for a fixed bug: a stale "Undo" pop-up (its review already undone from the
    # problem page) used to delete the review before it as well.
    def test_undo_with_review_id(self):
        pid = self.create()["id"]
        self.call("POST", f"/api/problems/{pid}/review", {"rating": 3})
        rated = self.call("POST", f"/api/problems/{pid}/review", {"rating": 1})["history"][0]["id"]
        self.call("POST", f"/api/problems/{pid}/undo", {})           # "Undo last review" button
        self.assertError(self.request("POST", f"/api/problems/{pid}/undo", {"review_id": rated}),
                         400, "already undone")
        self.assertEqual(count_rows(self.db_path, "reviews"), 1)
        latest = self.call("GET", f"/api/problems/{pid}")["history"][0]["id"]
        p = self.call("POST", f"/api/problems/{pid}/undo", {"review_id": latest})
        self.assertEqual(p["status"], "new")
        self.assertError(self.request("POST", f"/api/problems/{pid}/undo", {"review_id": "x"}),
                         400, "review_id")

    def test_review_path_with_trailing_slash(self):
        pid = self.create()["id"]
        self.call("POST", f"/api/problems/{pid}/review/", {"rating": 4})

    def test_concurrent_writes(self):
        results = []

        def worker(i):
            results.append(self.request("POST", "/api/problems",
                                        {"title": f"P{i}", "first_rating": (i % 4) + 1}).status)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(results, [201] * 8)
        self.assertEqual(count_rows(self.db_path, "problems"), 8)
        self.assertEqual(count_rows(self.db_path, "reviews"), 8)


class ReadEndpointsTest(ServerTestCase):
    def test_summary(self):
        self.create(first_rating=3)
        self.create()
        s = self.call("GET", "/api/summary")
        self.assertEqual(set(s), {"now", "day_start", "day_end", "today", "counts", "streak_days",
                                  "recall_rate_30d", "reviews_30d", "forecast", "settings"})
        self.assertEqual(s["forecast"][0]["date"], s["today"])
        self.assertEqual(s["counts"], {"total": 2, "active": 2, "suspended": 0, "new": 1,
                                       "due": 0, "scheduled": 1, "reviewed_today": 1})
        self.assertEqual(len(s["forecast"]), 14)
        self.assertEqual(sum(f["count"] for f in s["forecast"]), 1)
        self.assertEqual(s["streak_days"], 1)
        self.assertIsNone(s["recall_rate_30d"])

    def test_queue(self):
        g = self.create(title="G", tags=["graphs"])["id"]
        a = self.create(title="A", tags=["arrays"])["id"]
        q = self.call("GET", "/api/queue")
        self.assertEqual(set(q), {"due", "new", "new_waiting", "new_left_today",
                                  "new_introduced_today"})
        self.assertEqual([p["id"] for p in q["new"]], [g, a])
        self.assertEqual((q["due"], q["new_waiting"], q["new_left_today"]), ([], 2, 3))
        q = self.call("GET", "/api/queue?tag=graphs")
        self.assertEqual([p["id"] for p in q["new"]], [g])
        self.call("POST", f"/api/problems/{g}/review", {"rating": 3})
        q = self.call("GET", "/api/queue")
        self.assertEqual((q["new_introduced_today"], q["new_left_today"]), (1, 2))

    def test_tags(self):
        self.create(tags=["graphs", "bfs"])
        self.create(tags=["graphs"])
        self.assertEqual(self.call("GET", "/api/tags"),
                         {"tags": [{"tag": "graphs", "count": 2}, {"tag": "bfs", "count": 1}]})


class SettingsEndpointsTest(ServerTestCase):
    PREVIEW_KEYS = {"good_every_time", "hard_first_then_good", "hard_every_time"}

    def test_get(self):
        data = self.call("GET", "/api/settings")
        self.assertEqual(set(data), {"settings", "interval_preview"})
        self.assertEqual(data["settings"], {"desired_retention": 0.9, "maximum_interval": 60,
                                            "again_next_day": True, "new_per_day": 3,
                                            "day_starts_at": 4, "fsrs_parameters": None})
        self.assertEqual(set(data["interval_preview"]), self.PREVIEW_KEYS)
        self.assertEqual(data["interval_preview"]["good_every_time"][:3], [2.0, 11.0, 46.0])

    def test_patch_and_put(self):
        data = self.call("PATCH", "/api/settings", {"maximum_interval": 10, "new_per_day": 5})
        self.assertEqual((data["settings"]["maximum_interval"], data["settings"]["new_per_day"]),
                         (10, 5))
        self.assertEqual(max(data["interval_preview"]["good_every_time"]), 10.0)
        self.assertEqual(self.call("GET", "/api/settings")["settings"]["maximum_interval"], 10)
        data = self.call("PUT", "/api/settings", {"day_starts_at": 0})
        self.assertEqual((data["settings"]["day_starts_at"], data["settings"]["new_per_day"]),
                         (0, 5))

    def test_patch_reschedules(self):
        pid = self.create(first_rating=4)["id"]
        self.call("PATCH", "/api/settings", {"maximum_interval": 3})
        p = self.call("GET", f"/api/problems/{pid}")
        self.assertIn(p["due_in_days"], (2, 3))   # capped at 3 days, then fuzzed
        self.assertEqual(p["history"][0]["interval_days"], p["due_in_days"])
        self.call("PATCH", "/api/settings", {"desired_retention": 0.99})
        p = self.call("GET", f"/api/problems/{pid}")
        self.assertEqual(p["due_in_days"], 1)

    def test_patch_day_starts_at_moves_due_times(self):
        pid = self.create(first_rating=3)["id"]
        before = self.call("GET", f"/api/problems/{pid}")
        self.call("PATCH", "/api/settings", {"day_starts_at": 9})
        after = self.call("GET", f"/api/problems/{pid}")
        due_d = date.fromisoformat(after["due_date"])
        self.assertEqual(datetime.fromisoformat(after["due"]),
                         datetime(due_d.year, due_d.month, due_d.day, 9).astimezone())
        self.assertEqual(datetime.fromisoformat(before["due"]).astimezone().hour, 4)

    def test_patch_invalid(self):
        for body, fragment in (({"desired_retention": 0.5}, "desired_retention"),
                               ({"maximum_interval": 0}, "maximum_interval"),
                               ({"new_per_day": 101}, "new_per_day"),
                               ({"day_starts_at": 24}, "day_starts_at"),
                               ({"new_per_day": "many"}, "new_per_day"),
                               ({"theme": "dark"}, "unknown setting")):
            with self.subTest(body=body):
                self.assertError(self.request("PATCH", "/api/settings", body), 400, fragment)
        self.assertEqual(self.store.get_settings()["new_per_day"], 3)

    def test_method_not_allowed(self):
        for method in ("POST", "DELETE"):
            with self.subTest(method=method):
                self.assertError(self.request(method, "/api/settings", {}), 405)

    def test_preview(self):
        data = self.call("GET", "/api/settings/preview?desired_retention=0.8&maximum_interval=30")
        self.assertEqual(set(data), {"interval_preview"})
        self.assertEqual(set(data["interval_preview"]), self.PREVIEW_KEYS)
        self.assertEqual(max(data["interval_preview"]["good_every_time"]), 30.0)
        # the preview isn't saved
        self.assertEqual(self.store.get_settings()["maximum_interval"], 60)
        self.assertEqual(self.store.get_settings()["desired_retention"], 0.9)

    def test_preview_defaults_to_saved_settings(self):
        saved = self.call("GET", "/api/settings")["interval_preview"]
        self.assertEqual(self.call("GET", "/api/settings/preview")["interval_preview"], saved)

    def test_preview_higher_retention_is_shorter(self):
        lo = self.call("GET", "/api/settings/preview?desired_retention=0.8&maximum_interval=36500")
        hi = self.call("GET", "/api/settings/preview?desired_retention=0.97&maximum_interval=36500")
        self.assertGreater(sum(lo["interval_preview"]["good_every_time"]),
                           sum(hi["interval_preview"]["good_every_time"]))

    def test_preview_invalid(self):
        for query in ("desired_retention=abc", "desired_retention=0.5", "desired_retention=1",
                      "desired_retention=nan", "desired_retention=inf", "maximum_interval=0",
                      "maximum_interval=36501", "maximum_interval=1.5", "maximum_interval=x"):
            with self.subTest(query=query):
                self.assertError(self.request("GET", f"/api/settings/preview?{query}"), 400)


class ExportImportEndpointsTest(ServerTestCase):
    def test_export(self):
        self.create(first_rating=3, tags=["graphs"])
        self.create(title="New")
        resp = self.request("GET", "/api/export")
        self.assertEqual(resp.status, 200)
        self.assertSecurityHeaders(resp)
        data = resp.json()
        self.assertEqual(data["app"], "dsa-review")
        self.assertEqual(len(data["problems"]), 2)
        self.assertEqual(len(data["reviews"]), 1)
        disposition = resp.headers["Content-Disposition"]
        m = re.fullmatch(r'attachment; filename="dsa-review-backup-(\d{4}-\d{2}-\d{2})\.json"',
                         disposition)
        self.assertIsNotNone(m, disposition)
        self.assertEqual(m.group(1), data["exported_at"][:10])

    def test_import_round_trip(self):
        self.create(first_rating=3, tags=["graphs"])
        self.create(title="New")
        backup = self.call("GET", "/api/export")

        fresh = Store(self.tmp / "fresh" / "fresh.db")
        self.handler.store = fresh
        self.assertEqual(self.call("POST", "/api/import", backup), {"added": 2, "skipped": 0})
        self.assertEqual(self.call("POST", "/api/import", backup), {"added": 0, "skipped": 2})
        problems = self.call("GET", "/api/problems")["problems"]
        self.assertEqual(sorted(p["uid"] for p in problems),
                         sorted(p["uid"] for p in backup["problems"]))
        self.assertEqual(count_rows(fresh.db_path, "reviews"), 1)

    def test_import_rejects_non_backups(self):
        for body in ({}, {"app": "anki", "problems": []}):
            with self.subTest(body=body):
                self.assertError(self.request("POST", "/api/import", body), 400, "backup")
        self.assertError(self.request("POST", "/api/import", [{"app": "dsa-review"}]), 400,
                         "JSON object")
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    def test_import_invalid_problem_is_400_and_atomic(self):
        self.create()
        backup = self.call("GET", "/api/export")
        backup["problems"].append(dict(backup["problems"][0], uid="other",
                                       url="javascript:alert(1)"))
        fresh = Store(self.tmp / "fresh" / "fresh.db")
        self.handler.store = fresh
        self.assertError(self.request("POST", "/api/import", backup), 400, "url")
        self.assertEqual(count_rows(fresh.db_path, "problems"), 0)


# ============================================================================ errors
class ErrorHandlingTest(ServerTestCase):
    def test_unknown_problem_is_404(self):
        for method, path, body in (("GET", "/api/problems/999", _NO_BODY),
                                   ("PATCH", "/api/problems/999", {"title": "X"}),
                                   ("PATCH", "/api/problems/999", {}),
                                   ("PUT", "/api/problems/999", {"notes": "x"}),
                                   ("DELETE", "/api/problems/999", {}),
                                   ("POST", "/api/problems/999/review", {"rating": 3})):
            with self.subTest(method=method, path=path, body=body):
                self.assertError(self.request(method, path, body), 404, "999")

    # Regression test for a fixed bug (store.undo_last_review): an unknown id reports "no reviews to undo" as 400
    # instead of 404 like the other /api/problems/:id routes.
    def test_undo_unknown_problem_is_404(self):
        self.assertError(self.request("POST", "/api/problems/999/undo", {}), 404)

    def test_unknown_routes_are_404(self):
        for method, path in (("GET", "/api/nope"), ("POST", "/api/nope"),
                             ("GET", "/api/problems/abc"), ("GET", "/api/problems/1/review"),
                             ("GET", "/api/problems/1/undo"), ("POST", "/api/problems/1/edit"),
                             ("GET", "/api/problems/-1"), ("DELETE", "/api/summary"),
                             ("POST", "/api/queue"), ("PATCH", "/api/export"),
                             ("GET", "/api/import"), ("DELETE", "/api/problems"),
                             ("GET", "/api/"), ("GET", "/api"), ("GET", "/nope"),
                             ("GET", "/API/summary")):
            with self.subTest(method=method, path=path):
                body = _NO_BODY if method == "GET" else {}
                self.assertError(self.request(method, path, body), 404)

    def test_non_api_writes_are_405(self):
        for method, path in (("POST", "/"), ("DELETE", "/index.html"), ("PUT", "/static/app.js"),
                             ("PATCH", "/nope")):
            with self.subTest(method=method, path=path):
                self.assertError(self.request(method, path, {}), 405)

    def test_bad_json_is_400(self):
        for raw in (b"{not json", b"{'single': 'quotes'}", b'{"title": "x",}', b"\xff\xfe\x00",
                    b'{"title": "unterminated'):
            with self.subTest(raw=raw):
                self.assertError(self.request("POST", "/api/problems", raw=raw), 400,
                                 "not valid JSON")
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    def test_non_object_body_is_400(self):
        pid = self.create()["id"]
        for raw in (b"[1, 2]", b'"Two Sum"', b"42", b"null", b"true", b"[]"):
            for method, path in (("POST", "/api/problems"), ("PATCH", f"/api/problems/{pid}"),
                                 ("POST", f"/api/problems/{pid}/review"),
                                 ("PATCH", "/api/settings"), ("POST", "/api/import")):
                with self.subTest(raw=raw, path=path):
                    self.assertError(self.request(method, path, raw=raw), 400,
                                     "must be a JSON object")
        self.assertEqual(count_rows(self.db_path, "problems"), 1)

    def test_empty_body_on_create_is_400(self):
        self.assertError(self.request("POST", "/api/problems", raw=b""), 400, "title is required")

    def test_bad_rating_is_400(self):
        pid = self.create()["id"]
        for body in ({"rating": 0}, {"rating": 5}, {"rating": -1}, {"rating": "good"},
                     {"rating": None}, {}, {"rating": [3]}, {"rating": 3, "duration_ms": "x"}):
            with self.subTest(body=body):
                self.assertError(self.request("POST", f"/api/problems/{pid}/review", body), 400)
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    def test_bad_first_rating_is_400_and_creates_nothing(self):
        for value in (5, -1, "abc", [], {}, "3.5"):
            with self.subTest(value=value):
                self.assertError(self.request("POST", "/api/problems",
                                              {"title": "X", "first_rating": value}),
                                 400, "first_rating")
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    # Regression test for a fixed bug (store.create_problem): the problem is committed before the first review is
    # validated, so a bad duration_ms returns 400 but still creates the problem.
    def test_bad_duration_with_first_rating_creates_nothing(self):
        resp = self.request("POST", "/api/problems",
                            {"title": "X", "first_rating": 3, "duration_ms": "slow"})
        self.assertError(resp, 400, "duration_ms")
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    def test_validation_errors_are_400(self):
        pid = self.create()["id"]
        cases = (
            ("POST", "/api/problems", {"url": "https://x"}, "title is required"),
            ("POST", "/api/problems", {"title": "X", "url": "javascript:alert(1)"}, "url"),
            ("POST", "/api/problems", {"title": "X", "id": 5}, "unknown field"),
            ("POST", "/api/problems", {"title": "X" * 201}, "too long"),
            ("POST", "/api/problems", {"title": "X", "difficulty": "Insane"}, "difficulty"),
            ("POST", "/api/problems", {"title": "X", "tags": {"a": 1}}, "tags"),
            ("PATCH", f"/api/problems/{pid}", {"title": ""}, "title is required"),
            ("PATCH", f"/api/problems/{pid}", {"url": "data:text/html,hi"}, "url"),
            ("PATCH", f"/api/problems/{pid}", {"first_rating": 3}, "unknown field"),
        )
        for method, path, body, fragment in cases:
            with self.subTest(body=body):
                self.assertError(self.request(method, path, body), 400, fragment)
        self.assertEqual(count_rows(self.db_path, "problems"), 1)
        self.assertEqual(self.store.get_problem(pid)["title"], "Two Sum")

    # Regression test for a fixed bug (store.Store._clean): non-string difficulty -> AttributeError -> HTTP 500.
    def test_non_string_difficulty_is_400(self):
        with quiet_server_errors():
            resp = self.request("POST", "/api/problems", {"title": "X", "difficulty": 3})
        self.assertError(resp, 400)

    # Regression test for a fixed bug (store.review_problem / update_settings): numbers too large for int()
    # (JSON 1e400 parses as float inf) raise OverflowError -> HTTP 500.
    def test_huge_numbers_are_400(self):
        pid = self.create()["id"]
        with quiet_server_errors():
            resp = self.request("POST", f"/api/problems/{pid}/review",
                                raw=b'{"rating": 3, "duration_ms": 1e400}')
        self.assertError(resp, 400)

    def test_unexpected_exception_is_500_json(self):
        with mock.patch.object(self.store, "summary", side_effect=RuntimeError("boom")), \
                quiet_server_errors() as err:
            resp = self.request("GET", "/api/summary")
        # The browser gets a generic message; the details go to the terminal only.
        self.assertError(resp, 500, "unexpected server error")
        self.assertNotIn("boom", resp.json()["error"])
        self.assertIn("RuntimeError", err.getvalue())
        self.assertIn("boom", err.getvalue())

    def test_request_too_large(self):
        with mock.patch.object(server, "MAX_BODY", 64):
            body = {"title": "X", "notes": "n" * 200}
            self.assertError(self.request("POST", "/api/problems", body), 413, "too large")
            self.assertEqual(count_rows(self.db_path, "problems"), 0)
            # exactly at the limit is fine
            small = {"title": "Y", "notes": ""}
            small["notes"] = "n" * (64 - len(json.dumps(small)))
            self.assertEqual(len(json.dumps(small)), 64)
            self.assertEqual(self.request("POST", "/api/problems", small).status, 201)

    def test_default_body_limit(self):
        self.assertEqual(server.MAX_BODY, 20 * 1024 * 1024)
        resp = self.request("POST", "/api/import", headers={"Content-Length": str(21 * 1024 ** 2)},
                            raw=b"")
        self.assertError(resp, 413)

    # Regression test for a fixed bug (server.Handler._body): Content-Length isn't validated. A negative value makes
    # rfile.read(-1) block until the client closes the connection (the request thread
    # hangs); a non-numeric one raises ValueError -> HTTP 500. Both should be 400.
    def test_negative_content_length_is_rejected(self):
        request = (b"POST /api/import HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                   b"Content-Type: application/json\r\nContent-Length: -1\r\n\r\n{}" % self.port)
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(request)
            sock.settimeout(0.5)
            try:
                reply = sock.recv(65536)
            except socket.timeout:
                # Unblock the server thread politely: send EOF and drain its late reply,
                # so it doesn't write into a closed socket.
                sock.shutdown(socket.SHUT_WR)
                sock.settimeout(5)
                while sock.recv(65536):
                    pass
                self.fail("server hung waiting for the body of a Content-Length: -1 request")
        self.assertTrue(reply.startswith(b"HTTP/1.0 400"), reply[:40])

    def test_non_numeric_content_length_is_400(self):
        with quiet_server_errors():
            resp = self.request("POST", "/api/import", raw=b"{}",
                                headers={"Content-Length": "two"})
        self.assertError(resp, 400)


# ============================================================================ CSRF / DNS rebinding
class RequestGuardsTest(ServerTestCase):
    def test_writes_require_json_content_type(self):
        pid = self.create()["id"]
        stable = ("title", "status", "due", "reps", "suspended", "updated_at", "history")
        before = {k: self.store.get_problem(pid)[k] for k in stable}
        writes =(("POST", "/api/problems", {"title": "CSRF"}),
                  ("PATCH", f"/api/problems/{pid}", {"title": "Hacked"}),
                  ("PUT", f"/api/problems/{pid}", {"title": "Hacked"}),
                  ("DELETE", f"/api/problems/{pid}", {}),
                  ("POST", f"/api/problems/{pid}/review", {"rating": 1}),
                  ("POST", f"/api/problems/{pid}/undo", {}),
                  ("PATCH", "/api/settings", {"new_per_day": 50}),
                  ("POST", "/api/import", {"app": "dsa-review", "problems": []}))
        for ctype in (None, "text/plain", "text/plain; charset=utf-8",
                      "application/x-www-form-urlencoded", "multipart/form-data; boundary=x",
                      "application/jsonp", "application/json-patch+json", "text/json", ""):
            for method, path, body in writes:
                with self.subTest(ctype=ctype, method=method, path=path):
                    resp = self.request(method, path, body, content_type=ctype)
                    self.assertError(resp, 415, "Content-Type")
        # (preview/retrievability depend on the current time, so compare stable fields only)
        self.assertEqual({k: self.store.get_problem(pid)[k] for k in stable}, before)
        self.assertEqual(count_rows(self.db_path, "problems"), 1)
        self.assertEqual(self.store.get_settings()["new_per_day"], 3)

    def test_json_content_type_variants_accepted(self):
        for ctype in ("application/json", "application/json; charset=utf-8",
                      "Application/JSON", " application/json ;charset=UTF-8"):
            with self.subTest(ctype=ctype):
                resp = self.request("POST", "/api/problems", {"title": ctype}, content_type=ctype)
                self.assertEqual(resp.status, 201)

    def test_reads_do_not_need_content_type(self):
        self.assertEqual(self.request("GET", "/api/summary").status, 200)

    def test_foreign_origin_refused(self):
        pid = self.create()["id"]
        for origin in ("http://evil.example", "null", f"http://127.0.0.1:{self.port + 1}",
                       f"https://127.0.0.1:{self.port}", f"http://127.0.0.1:{self.port}.evil.example",
                       f"http://localhost.evil.example:{self.port}", "http://127.0.0.1",
                       f"http://[::1]:{self.port}", "file://"):
            for method, path, body in (("POST", "/api/problems", {"title": "CSRF"}),
                                       ("DELETE", f"/api/problems/{pid}", {}),
                                       ("PATCH", "/api/settings", {"new_per_day": 99}),
                                       ("POST", "/api/import", {"app": "dsa-review"})):
                with self.subTest(origin=origin, method=method, path=path):
                    resp = self.request(method, path, body, headers={"Origin": origin})
                    self.assertError(resp, 403, "cross-origin")
        self.assertEqual(count_rows(self.db_path, "problems"), 1)
        self.assertEqual(self.store.get_settings()["new_per_day"], 3)

    def test_same_origin_allowed(self):
        for origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}",
                       f"HTTP://LOCALHOST:{self.port}"):
            with self.subTest(origin=origin):
                resp = self.request("POST", "/api/problems", {"title": "ok"},
                                    headers={"Origin": origin})
                self.assertEqual(resp.status, 201)

    def test_foreign_origin_with_wrong_content_type_still_refused(self):
        resp = self.request("POST", "/api/problems", {"title": "x"}, content_type="text/plain",
                            headers={"Origin": "http://evil.example"})
        self.assertIn(resp.status, (403, 415))
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    def test_foreign_host_refused(self):
        self.create()
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1",
                     f"127.0.0.1:{self.port + 1}", f"127.0.0.1.evil.example:{self.port}",
                     f"localhost:{self.port}.evil.example", f"[::1]:{self.port}",
                     f"0.0.0.0:{self.port}", f"127.0.0.2:{self.port}", ""):
            for method, path, body in (("GET", "/api/summary", _NO_BODY),
                                       ("GET", "/api/export", _NO_BODY),
                                       ("GET", "/", _NO_BODY),
                                       ("GET", "/static/app.js", _NO_BODY),
                                       ("POST", "/api/problems", {"title": "rebind"})):
                with self.subTest(host=host, path=path):
                    resp = self.request(method, path, body, headers={"Host": host})
                    self.assertError(resp, 403, "Host")
                    self.assertNotIn(b"Two Sum", resp.body)
        self.assertEqual(count_rows(self.db_path, "problems"), 1)

    def test_missing_host_refused(self):
        self.assertError(self.request("GET", "/api/export", headers={"Host": None}), 403)
        self.assertError(self.request("POST", "/api/problems", {"title": "x"},
                                      headers={"Host": None}), 403)
        self.assertEqual(count_rows(self.db_path, "problems"), 0)

    def test_local_hosts_allowed(self):
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}",
                     f"LocalHost:{self.port}"):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/api/summary",
                                              headers={"Host": host}).status, 200)

    def test_cors_preflight_never_approved(self):
        resp = self.request("OPTIONS", "/api/problems", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"}, content_type=None)
        self.assertGreaterEqual(resp.status, 400)
        for header in ("Access-Control-Allow-Origin", "Access-Control-Allow-Methods",
                       "Access-Control-Allow-Headers"):
            self.assertIsNone(resp.headers[header])

    def test_no_cors_headers_on_reads(self):
        resp = self.request("GET", "/api/export", headers={"Origin": "http://evil.example"})
        self.assertEqual(resp.status, 200)
        self.assertIsNone(resp.headers["Access-Control-Allow-Origin"])
        self.assertIsNone(resp.headers["Access-Control-Allow-Credentials"])

    def test_security_headers_everywhere(self):
        pid = self.create()["id"]
        for method, path, body in (("GET", "/api/summary", _NO_BODY),
                                   ("GET", f"/api/problems/{pid}", _NO_BODY),
                                   ("POST", "/api/problems", {"title": "x"}),
                                   ("GET", "/api/problems/999", _NO_BODY),
                                   ("POST", "/api/problems", {}),
                                   ("GET", "/api/export", _NO_BODY),
                                   ("GET", "/", _NO_BODY),
                                   ("GET", "/nope", _NO_BODY),
                                   ("POST", "/", {})):
            with self.subTest(method=method, path=path):
                self.assertSecurityHeaders(self.request(method, path, body))
        self.assertSecurityHeaders(self.request("GET", "/", headers={"Host": "evil.example"}))
        self.assertSecurityHeaders(self.request("POST", "/api/problems", {}, content_type=None))


# ============================================================================ static files
class StaticFilesTest(ServerTestCase):
    FILES = {
        "index.html": b"<!doctype html><title>DSA Review</title><script src=/static/app.js></script>",
        "app.js": b"console.log('app');\n",
        "style.css": b"body { color: black }\n",
        "favicon.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
        "data.json": b'{"ok": true}',
        "notes.txt": b"not a served type",
        "sub/inner.js": b"export const inner = 1;\n",
    }
    SECRET = b"TOP-SECRET"

    def setUp(self):
        super().setUp()
        self.static = self.tmp / "static"
        for name, content in self.FILES.items():
            path = self.static / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        for name in ("secret.json", "secret.js", "static-secret.js"):
            (self.tmp / name).write_bytes(self.SECRET)
        patcher = mock.patch.object(server, "STATIC_DIR", self.static)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def assertServed(self, path, name, ctype_prefix=None):
        resp = self.get(path)
        self.assertEqual(resp.status, 200, (path, resp))
        self.assertEqual(resp.body, self.FILES[name])
        self.assertEqual(resp.headers["Content-Length"], str(len(self.FILES[name])))
        if ctype_prefix:
            self.assertTrue(resp.headers["Content-Type"].startswith(ctype_prefix),
                            resp.headers["Content-Type"])
        self.assertSecurityHeaders(resp)
        return resp

    def test_index(self):
        for path in ("/", "/index.html", "/?utm=1"):
            with self.subTest(path=path):
                resp = self.assertServed(path, "index.html", "text/html")
                self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")

    def test_app_js(self):
        resp = self.assertServed("/static/app.js", "app.js")
        self.assertEqual(resp.headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertServed("/static/app.js?v=123", "app.js")
        self.assertServed("/static/sub/inner.js", "sub/inner.js", "text/javascript")

    def test_other_types(self):
        # (no exact type for .css: Windows registry MIME mappings vary between machines)
        self.assertServed("/static/style.css", "style.css")
        self.assertServed("/static/data.json", "data.json", "application/json; charset=utf-8")
        self.assertServed("/static/favicon.svg", "favicon.svg", "image/svg+xml")
        self.assertServed("/favicon.ico", "favicon.svg", "image/svg+xml")

    def test_head(self):
        resp = self.request("HEAD", "/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body, b"")
        self.assertEqual(resp.headers["Content-Length"], str(len(self.FILES["index.html"])))

    def test_not_found(self):
        for path in ("/static/missing.js", "/static/notes.txt", "/static/", "/static",
                     "/static/sub", "/static/sub/", "/app.js", "/index.htm", "/secret.json",
                     "/static/APP.JS.bak", "/nope/", "/static/app.js.map"):
            with self.subTest(path=path):
                resp = self.get(path)
                self.assertError(resp, 404, "not found")

    def test_path_traversal(self):
        paths = [
            "/static/../secret.json",
            "/static/../secret.js",
            "/static/../static-secret.js",
            "/static/sub/../../secret.json",
            "/static/./../secret.json",
            "/static/sub/./../../secret.js",
            "/static/..%2fsecret.json",
            "/static/..%2Fsecret.json",
            "/static/%2e%2e/secret.json",
            "/static/%2e%2e%2fsecret.json",
            "/static/%2E%2E%2Fsecret.js",
            "/static/sub/..%2f..%2fsecret.json",
            "/static/%252e%252e%252fsecret.json",
            "/static/..%5csecret.json",
            "/static/..\\secret.json",
            "/static/sub\\..\\..\\secret.js",
            "/static/....//secret.json",
            "/static/..;/secret.json",
            "/static/..%00/secret.json",
            "/../secret.json",
            "/..%2fsecret.json",
            "/static/../../../../../../../../etc/passwd",
            "/static/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
            "/static/../static-secret.js?x=/static/app.js",
        ]
        secret_abs = (self.tmp / "secret.json").as_posix()
        if " " not in secret_abs:
            paths.append("/static/" + secret_abs)          # /static//tmp/... or /static/C:/...
            paths.append("/static//" + secret_abs.lstrip("/"))
        for path in paths:
            with self.subTest(path=path):
                resp = self.get(path)
                self.assertEqual(resp.status, 404, resp)
                self.assertNotIn(self.SECRET, resp.body)
                self.assertNotIn(b"root:", resp.body)

    def test_static_ignores_api_style_guards_but_checks_host(self):
        self.assertEqual(self.get("/static/app.js", headers={"Origin": "http://evil.example"}).status,
                         200)
        self.assertError(self.get("/static/app.js", headers={"Host": "evil.example"}), 403)


class LocalServerTest(unittest.TestCase):
    """Port handling in main(): a busy port must make the app move on to the next one."""

    def bind(self, port=0):
        srv = server.LocalServer(("127.0.0.1", port), server.Handler)
        self.addCleanup(srv.server_close)
        return srv

    def test_port_in_use_is_refused(self):
        first = self.bind()
        with self.assertRaises(OSError):
            self.bind(first.server_address[1])

    # Regression test for a fixed bug (server.main): http.server enables SO_REUSEADDR, which on
    # Windows lets a second process bind a port that is already listening, so running the app
    # twice shared port 8765 instead of falling back to 8766.
    def test_no_reuse_address_on_windows(self):
        with mock.patch.object(server, "WINDOWS", True):
            srv = self.bind()
        self.assertEqual(srv.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)
        with mock.patch.object(server, "WINDOWS", False):  # Mac/Linux keep fast restarts
            srv = self.bind()
        self.assertNotEqual(srv.socket.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR), 0)

    def test_main_falls_back_to_next_free_port(self):
        busy = self.bind()
        port = busy.server_address[1]
        started = []

        def fake_serve(srv, *args, **kwargs):
            started.append(srv.server_address[1])
            raise KeyboardInterrupt

        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(server.LocalServer, "serve_forever", fake_serve), \
                mock.patch.object(server.Handler, "store", None), \
                mock.patch.object(server.Handler, "port", 0), \
                contextlib.redirect_stdout(out):
            code = server.main(["--port", str(port), "--no-browser", "--data-dir", tmp])
        self.assertEqual(code, 0)
        self.assertEqual(len(started), 1)
        self.assertNotEqual(started[0], port)
        self.assertIn(f"http://127.0.0.1:{started[0]}/", out.getvalue())


class RealStaticDirTest(ServerTestCase):
    """The unpatched STATIC_DIR (app/static) - make sure app sources never leak."""

    def test_index_served(self):
        resp = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(resp.body, (server.STATIC_DIR / "index.html").read_bytes())

    def test_app_sources_not_reachable(self):
        for path in ("/static/../server.py", "/static/../store.py", "/static/..%2fserver.py",
                     "/static/%2e%2e/server.py", "/static/../../tests/test_server.py",
                     "/static/../../data/dsa_review.db", "/server.py", "/app/server.py",
                     "/static/../__pycache__/", "/static/..\\server.py"):
            with self.subTest(path=path):
                resp = self.request("GET", path)
                self.assertEqual(resp.status, 404, resp)
                self.assertNotIn(b"BaseHTTPRequestHandler", resp.body)
                self.assertNotIn(b"sqlite3", resp.body)


if __name__ == "__main__":
    unittest.main()
