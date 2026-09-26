"""Tests for app/store.py (SQLite storage, validation, queue/summary logic, backups)."""
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import (  # noqa: E402  (also puts app/ on sys.path)
    DAY, StoreTestCase, connect, count_rows, frozen_now, get_card, insert_review, local,
    problem_row, review_state_card, set_card, use_timezone, utc,
)

import scheduling as sched  # noqa: E402
from store import (  # noqa: E402
    DEFAULT_SETTINGS, SCHEMA_VERSION, TEXT_LIMITS, Invalid, NotFound, Store,
)

# A fixed "now": 10:30 local time on 2026-01-15 (far from any DST change), as UTC.
# Whatever the machine's timezone, its study date is TODAY.
NOW = local(2026, 1, 15, 10, 30).astimezone(timezone.utc)
TODAY = date(2026, 1, 15)
ANCHOR = utc(2026, 1, 15, 12)   # what FSRS sees for any review made on TODAY


def at_local(d: date, hours: float) -> datetime:
    """Local wall-clock time ``hours`` after midnight of ``d``, as UTC (DST-correct)."""
    return (datetime(d.year, d.month, d.day) + timedelta(hours=hours)).astimezone(timezone.utc)


def day_start_utc(d: date, hour: int = 4) -> str:
    """ISO string of the real instant study date ``d`` begins (what problem["due"] shows)."""
    return at_local(d, hour).isoformat()


def strip_id(card: dict) -> dict:
    return {k: v for k, v in card.items() if k != "card_id"}


def snapshot(db_path) -> dict:
    """Everything scheduling-related in a database (to compare before/after)."""
    with connect(db_path) as c:
        return {
            "problems": [tuple(r) for r in c.execute("SELECT id, card, due FROM problems ORDER BY id")],
            "reviews": [tuple(r) for r in c.execute(
                "SELECT id, problem_id, rating, reviewed_at, card_before, card_after FROM reviews "
                "ORDER BY id")],
        }


class FrozenStoreTestCase(StoreTestCase):
    """StoreTestCase with the app clock frozen at NOW (move it with self.advance())."""

    def setUp(self):
        super().setUp()
        patcher = frozen_now(NOW)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)

    def advance(self, **delta):
        self.clock.return_value = self.clock.return_value + timedelta(**delta)
        return self.clock.return_value

    def day_start(self):
        return self.store.day_bounds(self.clock.return_value)[0]


# ============================================================================ init
class StoreInitTest(StoreTestCase):
    def test_creates_parent_dirs_and_schema(self):
        self.assertTrue(self.db_path.is_file())
        with connect(self.db_path) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            version = c.execute("PRAGMA user_version").fetchone()[0]
        self.assertTrue({"problems", "reviews", "settings"} <= tables)
        self.assertEqual(version, SCHEMA_VERSION)

    def test_reopen_keeps_data(self):
        pid = self.add("Keep me")["id"]
        again = Store(self.db_path)
        self.assertEqual(again.get_problem(pid)["title"], "Keep me")

    def test_connections_enforce_foreign_keys(self):
        with self.store.conn() as c:
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO reviews (problem_id, rating, reviewed_at, card_before, "
                          "card_after) VALUES (999, 3, 'x', '{}', '{}')")

    def test_conn_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.store.conn() as c:
                c.execute("INSERT INTO settings(key, value) VALUES ('new_per_day', '7')")
                raise RuntimeError("boom")
        self.assertEqual(self.store.get_settings()["new_per_day"], 3)


# ============================================================================ create
class CreateProblemTest(FrozenStoreTestCase):
    FULL = {
        "title": "Two Sum", "url": "https://leetcode.com/problems/two-sum/",
        "source": "LeetCode", "difficulty": "Easy", "tags": ["Hash Map", "arrays"],
        "prompt": "Find two numbers adding to target.", "insight": "Store complements.",
        "notes": "O(n) time", "solution": "def two_sum(): ...", "language": "python",
    }

    def test_create_without_rating_is_new(self):
        p = self.store.create_problem(dict(self.FULL))
        self.assertIsInstance(p["id"], int)
        uuid.UUID(p["uid"])
        for key in ("title", "url", "source", "difficulty", "prompt", "insight", "notes",
                    "solution", "language"):
            self.assertEqual(p[key], self.FULL[key], key)
        self.assertEqual(p["tags"], ["hash-map", "arrays"])
        self.assertFalse(p["suspended"])
        self.assertEqual(p["created_at"], NOW.isoformat())
        self.assertEqual(p["updated_at"], NOW.isoformat())
        self.assertEqual(p["status"], "new")
        for key in ("due", "due_date", "due_in_days", "overdue_days", "last_review", "stability",
                    "fsrs_difficulty", "retrievability", "last_rating"):
            self.assertIsNone(p[key], key)
        self.assertEqual((p["reps"], p["lapses"]), (0, 0))
        self.assertEqual(p["history"], [])
        self.assertEqual({k: v["interval_days"] for k, v in p["preview"].items()},
                         {"again": 1.0, "hard": 1.0, "good": 2.0, "easy": 8.0})
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)
        self.assertEqual(get_card(self.db_path, p["id"]).card_id, p["id"])

    def test_defaults(self):
        p = self.add("Only a title")
        self.assertEqual((p["url"], p["source"], p["difficulty"], p["tags"], p["prompt"],
                          p["insight"], p["notes"], p["solution"], p["language"]),
                         ("", "", "", [], "", "", "", "", "python"))
        self.assertFalse(p["suspended"])

    def test_uids_are_unique(self):
        uids = {self.add(f"P{i}")["uid"] for i in range(5)}
        self.assertEqual(len(uids), 5)

    def test_input_dict_not_mutated(self):
        data = dict(self.FULL)
        self.store.create_problem(data, 3)
        self.assertEqual(data, self.FULL)

    def test_create_with_first_rating_is_scheduled(self):
        expected_gap = {1: (1, 1), 2: (1, 1), 3: (2, 2), 4: (5, 11)}
        for rating in (1, 2, 3, 4):
            with self.subTest(rating=rating):
                p = self.add(f"R{rating}", first_rating=rating)
                self.assertEqual(p["status"], "scheduled")
                self.assertEqual(p["reps"], 1)
                self.assertEqual(p["lapses"], 1 if rating == 1 else 0)
                self.assertEqual(p["last_rating"], rating)
                self.assertEqual(p["last_review"], NOW.isoformat())  # the real review time
                self.assertEqual(p["retrievability"], 1.0)
                self.assertEqual(p["overdue_days"], 0)
                self.assertIsNotNone(p["stability"])
                self.assertTrue(1 <= p["fsrs_difficulty"] <= 10)
                days = p["due_in_days"]
                lo, hi = expected_gap[rating]
                self.assertTrue(lo <= days <= hi, days)
                due_d = TODAY + timedelta(days=days)
                self.assertEqual(p["due_date"], due_d.isoformat())
                self.assertEqual(p["due"], day_start_utc(due_d))
                self.assertEqual(p["due"], sched.study_day_start(due_d).isoformat())
                # the stored FSRS card only ever sees day anchors
                card = get_card(self.db_path, p["id"])
                self.assertEqual((card.last_review, card.due), (ANCHOR, ANCHOR + days * DAY))
                self.assertEqual(problem_row(self.db_path, p["id"])["due"],
                                 (ANCHOR + days * DAY).isoformat())
                self.assertEqual(len(p["history"]), 1)
                h = p["history"][0]
                self.assertEqual(h["rating"], rating)
                self.assertEqual(h["rating_name"], sched.RATING_NAMES[rating])
                self.assertEqual(h["kind"], "added")
                self.assertEqual(h["reviewed_at"], NOW.isoformat())
                self.assertEqual(h["next_due"], p["due"])
                self.assertEqual(h["interval_days"], days)
                self.assertEqual(h["stability"], p["stability"])
                with connect(self.db_path) as c:
                    row = c.execute("SELECT * FROM reviews WHERE problem_id = ?",
                                    (p["id"],)).fetchone()
                self.assertEqual(row["kind"], "added")
                self.assertEqual(row["reviewed_at"], NOW.isoformat())
                self.assertIsNone(json.loads(row["card_before"])["last_review"])
                self.assertEqual(json.loads(row["card_after"])["last_review"], ANCHOR.isoformat())

    def test_first_rating_duration_is_stored(self):
        p = self.store.create_problem({"title": "Timed"}, 3, 90000)
        self.assertEqual(p["history"][0]["duration_ms"], 90000)

    def test_duration_is_clamped(self):
        p = self.store.create_problem({"title": "Long"}, 3, 10 ** 12)
        self.assertEqual(p["history"][0]["duration_ms"], 24 * 3600 * 1000)
        p = self.store.review_problem(p["id"], 3, -5)
        self.assertEqual(p["history"][0]["duration_ms"], 0)
        p = self.store.review_problem(p["id"], 3, "1500")
        self.assertEqual(p["history"][0]["duration_ms"], 1500)

    def test_first_rating_zero_or_none_means_new(self):
        for value in (None, 0):
            self.assertEqual(self.store.create_problem({"title": "X"}, value)["status"], "new")
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    # Regression test for a fixed bug (store.create_problem): the problem row is INSERTed and committed *before*
    # review_problem() validates first_rating/duration_ms. A bad duration_ms (or an
    # out-of-range first_rating when called directly) raises Invalid but leaves a
    # half-created, never-reviewed problem behind. Reachable over HTTP:
    # POST /api/problems {"title": "X", "first_rating": 3, "duration_ms": "abc"} -> 400,
    # yet "X" now exists in the "new" queue.
    def test_invalid_first_review_leaves_no_problem_behind(self):
        with self.assertRaises(Invalid):
            self.store.create_problem({"title": "Orphan"}, 3, "not a number")
        self.assertEqual(count_rows(self.db_path, "problems"), 0)


# ============================================================================ validation
class ValidationTest(StoreTestCase):
    def assertInvalid(self, data, partial=False, msg=None):
        with self.assertRaises(Invalid) as ctx:
            if partial:
                self.store.update_problem(self.pid, data)
            else:
                self.store.create_problem(data)
        if msg:
            self.assertIn(msg, str(ctx.exception))

    def setUp(self):
        super().setUp()
        self.pid = self.add("Existing")["id"]

    def test_title_required(self):
        for data in ({}, {"title": ""}, {"title": "   \n\t"}, {"title": None},
                     {"notes": "no title"}):
            with self.subTest(data=data):
                self.assertInvalid(data, msg="title is required")
        self.assertEqual(count_rows(self.db_path, "problems"), 1)

    def test_title_trimmed(self):
        self.assertEqual(self.add("  Two Sum \n")["title"], "Two Sum")

    def test_text_must_be_string(self):
        for key in TEXT_LIMITS:
            for bad in (123, ["x"], {"a": 1}, True):
                with self.subTest(key=key, bad=bad):
                    data = {"title": "T", key: bad}
                    self.assertInvalid(data, msg=f"{key} must be text")

    def test_text_limits(self):
        for key, limit in TEXT_LIMITS.items():
            ok = "a" * limit if key != "url" else "https://" + "a" * (limit - 8)
            with self.subTest(key=key):
                data = {"title": "T", key: ok}
                self.assertEqual(self.store.create_problem(data)[key], ok)
                data = {"title": "T", key: ok + "a"}
                self.assertInvalid(data, msg="too long")
                self.assertInvalid({key: ok + "a"}, partial=True, msg="too long")

    def test_long_text_fields_keep_whitespace(self):
        p = self.add("T", notes="  indented\n", solution="\tdef f():\n\t\tpass\n",
                     prompt=" x ")
        self.assertEqual((p["notes"], p["solution"], p["prompt"]),
                         ("  indented\n", "\tdef f():\n\t\tpass\n", " x "))
        p = self.add("T", source="  LeetCode ", insight=" idea ", language=" go ")
        self.assertEqual((p["source"], p["insight"], p["language"]), ("LeetCode", "idea", "go"))

    def test_none_text_becomes_empty(self):
        p = self.add("T", url=None, notes=None, source=None)
        self.assertEqual((p["url"], p["notes"], p["source"]), ("", "", ""))

    def test_language_defaults_to_python(self):
        self.assertEqual(self.add("T", language="")["language"], "python")
        self.assertEqual(self.add("T", language="   ")["language"], "python")
        self.assertEqual(self.add("T", language="C++")["language"], "C++")
        p = self.store.update_problem(self.pid, {"language": ""})
        self.assertEqual(p["language"], "python")

    def test_url_accepted(self):
        for url in ("", "http://example.com", "https://leetcode.com/problems/two-sum/",
                    "HTTPS://LEETCODE.COM/x", "  https://padded.example/  "):
            with self.subTest(url=url):
                self.assertEqual(self.add("T", url=url)["url"], url.strip())

    def test_url_rejected(self):
        for url in ("javascript:alert(1)", "JavaScript:alert(document.cookie)",
                    "  javascript:alert(1)", "javascript://%0Aalert(1)",
                    "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)",
                    "file:///etc/passwd", "ftp://example.com", "//evil.example",
                    "leetcode.com/problems", "https:/missing-slash", "mailto:a@b.c",
                    "\njavascript:alert(1)"):
            with self.subTest(url=url):
                self.assertInvalid({"title": "T", "url": url}, msg="url must start with")
                self.assertInvalid({"url": url}, partial=True)
        self.assertEqual(self.store.get_problem(self.pid)["url"], "")

    def test_difficulty_normalized(self):
        for given, stored in (("Easy", "Easy"), ("medium", "Medium"), ("HARD", "Hard"),
                              (" easy ", "Easy"), ("", ""), (None, "")):
            with self.subTest(given=given):
                self.assertEqual(self.add("T", difficulty=given)["difficulty"], stored)

    def test_difficulty_rejected(self):
        for bad in ("Extreme", "Very Hard", "e", "Easy!", "Medium-Hard"):
            with self.subTest(bad=bad):
                self.assertInvalid({"title": "T", "difficulty": bad}, msg="difficulty must be")

    # Regression test for a fixed bug (store.Store._clean): a non-string difficulty (e.g. 5, true, ["Easy"]) hits
    # `(data["difficulty"] or "").strip()` and raises AttributeError instead of Invalid,
    # so the API answers HTTP 500 instead of 400.
    def test_difficulty_non_string_rejected_cleanly(self):
        self.assertInvalid({"title": "T", "difficulty": 5})

    def test_tags_slugged(self):
        p = self.add("T", tags=["Dynamic Programming", "  Two   Pointers ", "C++", "C#",
                                "BFS/DFS", "--graph--", "Hash_Map"])
        self.assertEqual(p["tags"], ["dynamic-programming", "two-pointers", "c++", "c#",
                                     "bfs-dfs", "graph", "hash-map"])

    def test_non_english_tags_are_kept(self):
        p = self.add("T", tags=["그래프", "Árbol Binario", "x" * 39 + "-y"])
        self.assertEqual(p["tags"], ["그래프", "árbol-binario", "x" * 39])

    def test_rating_must_be_a_real_number(self):
        pid = self.add("R")["id"]
        for bad in (True, False, 2.5, "two", None, 0, 5):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.review_problem(pid, bad)

    def test_tags_from_comma_string(self):
        p = self.add("T", tags="Arrays, two pointers ,, sliding window,")
        self.assertEqual(p["tags"], ["arrays", "two-pointers", "sliding-window"])

    def test_tags_deduplicated_after_slugging(self):
        p = self.add("T", tags=["Graph", "graph", "GRAPH ", "Two Pointers", "two-pointers",
                                "two_pointers"])
        self.assertEqual(p["tags"], ["graph", "two-pointers"])

    def test_empty_tags_dropped(self):
        self.assertEqual(self.add("T", tags=["", "   ", "---", "!!"])["tags"], [])
        self.assertEqual(self.add("T", tags=[])["tags"], [])
        self.assertEqual(self.add("T", tags="")["tags"], [])

    def test_tags_limited_to_20(self):
        p = self.add("T", tags=[f"tag{i}" for i in range(30)])
        self.assertEqual(p["tags"], [f"tag{i}" for i in range(20)])

    def test_tag_length_limited_to_40(self):
        p = self.add("T", tags=["x" * 100])
        self.assertEqual(p["tags"], ["x" * 40])

    def test_tags_must_be_list_or_string(self):
        for bad in ({"a": 1}, 5, 1.5):
            with self.subTest(bad=bad):
                self.assertInvalid({"title": "T", "tags": bad}, msg="tags must be a list")

    def test_unknown_fields_rejected(self):
        for key in ("id", "uid", "card", "due", "status", "created_at", "updated_at",
                    "first_rating", "stability", "__proto__", "title; DROP TABLE problems"):
            with self.subTest(key=key):
                self.assertInvalid({"title": "T", key: "x"}, msg="unknown field")
                self.assertInvalid({key: "x"}, partial=True, msg="unknown field")
        self.assertEqual(count_rows(self.db_path, "problems"), 1)

    def test_suspended_coerced_to_bool(self):
        self.assertTrue(self.add("T", suspended=1)["suspended"])
        self.assertFalse(self.add("T", suspended=0)["suspended"])
        self.assertEqual(self.add("T", suspended=True)["status"], "suspended")

    def test_failed_validation_inserts_nothing(self):
        self.assertInvalid({"title": "T", "url": "javascript:x"})
        self.assertInvalid({"title": "T" * 201})
        self.assertEqual(count_rows(self.db_path, "problems"), 1)

    def test_deck_defaults_to_main(self):
        self.assertEqual(self.add("T")["deck"], "main")

    def test_deck_accepted_on_create(self):
        self.assertEqual(self.add("T", deck="neetcode")["deck"], "neetcode")
        self.assertEqual(self.add("T", deck="main")["deck"], "main")

    def test_deck_rejected_on_create(self):
        for bad in ("", "Main", "NEETCODE", "other", None, 5, ["main"]):
            with self.subTest(bad=bad):
                self.assertInvalid({"title": "T", "deck": bad}, msg="deck must be")

    def test_deck_accepted_on_update(self):
        p = self.store.update_problem(self.pid, {"deck": "neetcode"})
        self.assertEqual(p["deck"], "neetcode")
        p = self.store.update_problem(self.pid, {"deck": "main"})
        self.assertEqual(p["deck"], "main")

    def test_deck_rejected_on_update(self):
        self.assertInvalid({"deck": "bogus"}, partial=True, msg="deck must be")
        self.assertEqual(self.store.get_problem(self.pid)["deck"], "main")

    def test_changing_deck_does_not_touch_scheduling(self):
        pid = self.add("T", first_rating=3)["id"]
        before = self.store.get_problem(pid)
        self.store.update_problem(pid, {"deck": "neetcode"})
        after = self.store.get_problem(pid)
        for key in ("due", "due_date", "stability", "fsrs_difficulty", "reps", "lapses"):
            self.assertEqual(before[key], after[key])


# ============================================================================ update / suspend / delete
class UpdateProblemTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.p = self.add("Two Sum", first_rating=3, notes="old notes", tags=["arrays"],
                          url="https://a.example")
        self.pid = self.p["id"]

    def test_full_update(self):
        later = self.advance(hours=1)
        patch = {"title": "Three Sum", "url": "https://b.example", "source": "NeetCode",
                 "difficulty": "Medium", "tags": ["Two Pointers"], "prompt": "p",
                 "insight": "sort first", "notes": "n", "solution": "s", "language": "java"}
        p = self.store.update_problem(self.pid, patch)
        for key, value in patch.items():
            if key != "tags":
                self.assertEqual(p[key], value, key)
        self.assertEqual(p["tags"], ["two-pointers"])
        self.assertEqual(p["updated_at"], later.isoformat())
        self.assertEqual(p["created_at"], NOW.isoformat())

    def test_partial_update_keeps_other_fields(self):
        p = self.store.update_problem(self.pid, {"notes": "new notes"})
        self.assertEqual(p["notes"], "new notes")
        self.assertEqual(p["title"], "Two Sum")
        self.assertEqual(p["tags"], ["arrays"])
        self.assertEqual(p["url"], "https://a.example")

    def test_update_does_not_touch_schedule(self):
        before = problem_row(self.db_path, self.pid)
        self.store.update_problem(self.pid, {"title": "Renamed", "tags": "x"})
        after = problem_row(self.db_path, self.pid)
        self.assertEqual((before["card"], before["due"]), (after["card"], after["due"]))
        self.assertEqual(count_rows(self.db_path, "reviews"), 1)

    def test_clear_optional_fields(self):
        p = self.store.update_problem(self.pid, {"url": None, "notes": "", "tags": []})
        self.assertEqual((p["url"], p["notes"], p["tags"]), ("", "", []))

    def test_empty_patch_is_noop(self):
        self.advance(hours=1)
        p = self.store.update_problem(self.pid, {})
        self.assertEqual(p["updated_at"], NOW.isoformat())
        self.assertEqual(p["title"], "Two Sum")

    def test_invalid_update_changes_nothing(self):
        before = problem_row(self.db_path, self.pid)
        for patch in ({"title": ""}, {"title": "OK", "url": "javascript:alert(1)"},
                      {"title": "OK", "bogus": 1}, {"title": "OK", "difficulty": "Insane"}):
            with self.subTest(patch=patch), self.assertRaises(Invalid):
                self.store.update_problem(self.pid, patch)
        self.assertEqual(problem_row(self.db_path, self.pid), before)

    def test_unknown_problem(self):
        with self.assertRaises(NotFound):
            self.store.update_problem(999, {"title": "X"})
        with self.assertRaises(NotFound):
            self.store.update_problem(999, {})

    def test_suspend_and_unsuspend(self):
        p = self.store.update_problem(self.pid, {"suspended": True})
        self.assertTrue(p["suspended"])
        self.assertEqual(p["status"], "suspended")
        self.assertIsNotNone(p["due"])  # schedule is kept while suspended
        # even when overdue, a suspended problem stays out of the queue
        set_card(self.db_path, self.pid, review_state_card(
            last_review=ANCHOR - 10 * DAY, stability=2.0, due=ANCHOR - 5 * DAY))
        self.assertEqual(self.store.queue()["due"], [])
        self.assertEqual(self.store.list_problems(status="suspended")[0]["id"], self.pid)
        p = self.store.update_problem(self.pid, {"suspended": False})
        self.assertFalse(p["suspended"])
        self.assertEqual(p["status"], "due")
        self.assertEqual([q["id"] for q in self.store.queue()["due"]], [self.pid])

    def test_suspended_new_problem_not_in_new_queue(self):
        new = self.add("New one")
        self.store.update_problem(new["id"], {"suspended": True})
        q = self.store.queue()
        self.assertEqual(q["new"], [])
        self.assertEqual(q["new_waiting"], 0)


class DeleteProblemTest(FrozenStoreTestCase):
    def test_delete_cascades_reviews(self):
        a = self.add("A", first_rating=3)["id"]
        self.store.review_problem(a, 4)
        b = self.add("B", first_rating=2)["id"]
        self.assertEqual(count_rows(self.db_path, "reviews", "problem_id = ?", (a,)), 2)
        self.store.delete_problem(a)
        with self.assertRaises(NotFound):
            self.store.get_problem(a)
        self.assertEqual(count_rows(self.db_path, "reviews", "problem_id = ?", (a,)), 0)
        self.assertEqual(count_rows(self.db_path, "reviews", "problem_id = ?", (b,)), 1)
        self.assertEqual([p["id"] for p in self.store.list_problems()], [b])

    def test_delete_unknown(self):
        with self.assertRaises(NotFound):
            self.store.delete_problem(12345)
        pid = self.add("A")["id"]
        self.store.delete_problem(pid)
        with self.assertRaises(NotFound):
            self.store.delete_problem(pid)

    def test_ids_not_reused(self):
        pid = self.add("A")["id"]
        self.store.delete_problem(pid)
        self.assertGreater(self.add("B")["id"], pid)


# ============================================================================ drafts
class DraftTest(FrozenStoreTestCase):
    def test_empty_draft_by_default(self):
        pid = self.add("A")["id"]
        d = self.store.get_draft(pid)
        self.assertEqual(d, {"code": "", "language": "python", "updated_at": None})

    def test_save_and_get_round_trip(self):
        pid = self.add("A")["id"]
        saved = self.store.save_draft(pid, "print('hi')", "python")
        self.assertEqual(saved["code"], "print('hi')")
        self.assertEqual(saved["language"], "python")
        self.assertIsNotNone(saved["updated_at"])
        got = self.store.get_draft(pid)
        self.assertEqual(got, saved)

    def test_save_overwrites_previous_draft(self):
        pid = self.add("A")["id"]
        self.store.save_draft(pid, "first")
        self.store.save_draft(pid, "second")
        self.assertEqual(self.store.get_draft(pid)["code"], "second")
        self.assertEqual(count_rows(self.db_path, "drafts"), 1)

    def test_language_defaults_to_python(self):
        pid = self.add("A")["id"]
        saved = self.store.save_draft(pid, "x = 1", "")
        self.assertEqual(saved["language"], "python")
        saved2 = self.store.save_draft(pid, "x = 1", None)
        self.assertEqual(saved2["language"], "python")

    def test_code_over_limit_is_rejected(self):
        pid = self.add("A")["id"]
        with self.assertRaises(Invalid):
            self.store.save_draft(pid, "x" * 100001)
        # exactly at the limit is fine
        self.store.save_draft(pid, "x" * 100000)

    def test_non_string_code_is_rejected(self):
        pid = self.add("A")["id"]
        with self.assertRaises(Invalid):
            self.store.save_draft(pid, 12345)

    def test_unknown_problem_raises_not_found(self):
        with self.assertRaises(NotFound):
            self.store.get_draft(99999)
        with self.assertRaises(NotFound):
            self.store.save_draft(99999, "code")

    def test_delete_problem_cascades_draft(self):
        pid = self.add("A")["id"]
        self.store.save_draft(pid, "print(1)")
        self.assertEqual(count_rows(self.db_path, "drafts"), 1)
        self.store.delete_problem(pid)
        self.assertEqual(count_rows(self.db_path, "drafts"), 0)


# ============================================================================ review / undo
class ReviewUndoTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add("Two Sum")["id"]

    def test_review_updates_stats_and_history(self):
        self.store.review_problem(self.pid, 3, 1000)
        self.advance(days=2)
        self.store.review_problem(self.pid, 1)
        self.advance(days=1)
        p = self.store.review_problem(self.pid, 2, 3000)
        self.assertEqual((p["reps"], p["lapses"], p["last_rating"]), (3, 1, 2))
        self.assertEqual([h["rating"] for h in p["history"]], [2, 1, 3])  # newest first
        self.assertEqual([h["rating_name"] for h in p["history"]], ["hard", "again", "good"])
        self.assertEqual([h["duration_ms"] for h in p["history"]], [3000, None, 1000])
        self.assertEqual(p["history"][0]["reviewed_at"], (NOW + 3 * DAY).isoformat())
        ids = [h["id"] for h in p["history"]]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(p["history"][0]["next_due"], p["due"])
        self.assertEqual(p["history"][0]["interval_days"], p["due_in_days"])
        self.assertEqual([h["kind"] for h in p["history"]], ["review"] * 3)
        self.assertEqual(p["last_review"], (NOW + 3 * DAY).isoformat())
        card = get_card(self.db_path, self.pid)
        self.assertEqual(card.last_review, ANCHOR + 3 * DAY)
        self.assertEqual(card.due, ANCHOR + (3 + p["due_in_days"]) * DAY)
        self.assertEqual(p["status"], "scheduled")
        with connect(self.db_path) as c:
            kinds = {r[0] for r in c.execute("SELECT kind FROM reviews")}
        self.assertEqual(kinds, {"review"})

    def test_review_card_chain_is_consistent(self):
        self.store.review_problem(self.pid, 3)
        self.advance(days=2)
        self.store.review_problem(self.pid, 3)
        with connect(self.db_path) as c:
            rows = c.execute("SELECT card_before, card_after FROM reviews ORDER BY id").fetchall()
        self.assertEqual(json.loads(rows[1]["card_before"]), json.loads(rows[0]["card_after"]))
        self.assertEqual(json.loads(rows[1]["card_after"]),
                         json.loads(problem_row(self.db_path, self.pid)["card"]))

    def test_invalid_ratings(self):
        for bad in (0, 5, -1, "x", "", None, [], {}, "3.5"):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.review_problem(self.pid, bad)
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    def test_string_rating_accepted(self):
        self.assertEqual(self.store.review_problem(self.pid, "4")["last_rating"], 4)

    def test_invalid_duration(self):
        for bad in ("abc", [], {}):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.review_problem(self.pid, 3, bad)
        self.assertEqual(count_rows(self.db_path, "reviews"), 0)

    # Regression test for a fixed bug (store.review_problem): duration_ms=inf (JSON `1e400` or Python's `Infinity`)
    # raises OverflowError from int(), which is not caught -> HTTP 500 instead of 400.
    def test_infinite_duration_rejected_cleanly(self):
        with self.assertRaises(Invalid):
            self.store.review_problem(self.pid, 3, float("inf"))

    def test_review_unknown_problem(self):
        with self.assertRaises(NotFound):
            self.store.review_problem(999, 3)

    def test_undo_restores_previous_card_exactly(self):
        original = problem_row(self.db_path, self.pid)
        self.store.review_problem(self.pid, 4)
        self.advance(days=9)
        row1 = problem_row(self.db_path, self.pid)
        view1 = self.store.get_problem(self.pid)
        self.store.review_problem(self.pid, 1, 5000)
        self.assertNotEqual(problem_row(self.db_path, self.pid)["card"], row1["card"])

        undone = self.store.undo_last_review(self.pid)
        self.assertEqual(problem_row(self.db_path, self.pid), row1)
        self.assertEqual(undone, view1)

        undone = self.store.undo_last_review(self.pid)
        self.assertEqual(problem_row(self.db_path, self.pid), original)
        self.assertEqual(undone["status"], "new")
        self.assertEqual((undone["reps"], undone["due"], undone["history"]), (0, None, []))
        self.assertEqual([p["id"] for p in self.store.queue()["new"]], [self.pid])

        with self.assertRaises(Invalid):
            self.store.undo_last_review(self.pid)

    def test_undo_removes_latest_review_even_with_equal_timestamps(self):
        self.store.review_problem(self.pid, 3)
        self.store.review_problem(self.pid, 1)  # same frozen timestamp
        p = self.store.undo_last_review(self.pid)
        self.assertEqual(p["last_rating"], 3)
        self.assertEqual(p["reps"], 1)

    def test_undo_first_rating_makes_problem_new(self):
        pid = self.add("Solved", first_rating=4)["id"]
        p = self.store.undo_last_review(pid)
        self.assertEqual(p["status"], "new")
        self.assertEqual(count_rows(self.db_path, "reviews", "problem_id = ?", (pid,)), 0)

    def test_undo_without_reviews(self):
        with self.assertRaises(Invalid):
            self.store.undo_last_review(self.pid)

    def test_undo_only_affects_that_problem(self):
        other = self.add("Other", first_rating=3)["id"]
        self.store.review_problem(self.pid, 3)
        before = problem_row(self.db_path, other)
        self.store.undo_last_review(self.pid)
        self.assertEqual(problem_row(self.db_path, other), before)
        self.assertEqual(count_rows(self.db_path, "reviews", "problem_id = ?", (other,)), 1)

    # Regression test for a fixed bug (store.undo_last_review): an unknown problem id raises Invalid ("no reviews to
    # undo") instead of NotFound, so POST /api/problems/999/undo answers 400, not 404
    # like every other /api/problems/:id route.
    def test_undo_unknown_problem_is_not_found(self):
        with self.assertRaises(NotFound):
            self.store.undo_last_review(999)

    def test_undo_with_matching_review_id(self):
        self.store.review_problem(self.pid, 3)
        self.advance(days=2)
        latest = self.store.review_problem(self.pid, 1)["history"][0]["id"]
        p = self.store.undo_last_review(self.pid, latest)
        self.assertEqual((p["reps"], p["last_rating"]), (1, 3))

    # Regression test for a fixed bug (store.undo_last_review / the "Undo" pop-up): the pop-up
    # shown after rating keeps its Undo button for 10 s. If that review had already been undone
    # another way (e.g. "Undo last review" on the problem page), pressing it deleted the
    # *previous* review too, silently losing real history. Now the pop-up sends the id of
    # its review and nothing changes if that review is gone.
    def test_undo_with_stale_review_id_changes_nothing(self):
        self.store.review_problem(self.pid, 3)
        self.advance(days=2)
        rated = self.store.review_problem(self.pid, 1)["history"][0]["id"]
        self.store.undo_last_review(self.pid)          # undone elsewhere first
        before = problem_row(self.db_path, self.pid)
        with self.assertRaisesRegex(Invalid, "already undone"):
            self.store.undo_last_review(self.pid, rated)
        self.assertEqual(problem_row(self.db_path, self.pid), before)
        self.assertEqual(count_rows(self.db_path, "reviews"), 1)

    def test_undo_with_superseded_review_id_changes_nothing(self):
        first = self.store.review_problem(self.pid, 3)["history"][0]["id"]
        self.advance(days=2)
        self.store.review_problem(self.pid, 4)          # rated again since
        with self.assertRaises(Invalid):
            self.store.undo_last_review(self.pid, first)
        self.assertEqual(count_rows(self.db_path, "reviews"), 2)

    def test_undo_review_id_must_be_a_positive_integer(self):
        self.store.review_problem(self.pid, 3)
        for bad in ("x", True, 1.5, 0, -3, [], {}):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.undo_last_review(self.pid, bad)
        self.assertEqual(count_rows(self.db_path, "reviews"), 1)


# ============================================================================ listing
class ListProblemsTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.add("Two Sum", tags=["arrays", "hash-map"], source="LeetCode")["id"]
        self.b = self.add("Course Schedule", tags=["graphs"], insight="Topological SORT",
                          first_rating=3)["id"]
        self.c = self.add("Word Ladder", tags=["graphs", "bfs"], source="NeetCode",
                          first_rating=4)["id"]

    def ids(self, **kw):
        return [p["id"] for p in self.store.list_problems(**kw)]

    def test_sorted_by_due(self):
        # new problem is due "now" (creation time); scheduled ones later
        self.assertEqual(self.ids(), [self.a, self.b, self.c])
        set_card(self.db_path, self.c, review_state_card(last_review=ANCHOR - 5 * DAY,
                                                         stability=1.0, due=ANCHOR - 4 * DAY))
        self.assertEqual(self.ids(), [self.c, self.a, self.b])

    def test_search(self):
        self.assertEqual(self.ids(q="two"), [self.a])
        self.assertEqual(self.ids(q="TOPOLOGICAL"), [self.b])  # insight
        self.assertEqual(self.ids(q="neetcode"), [self.c])     # source
        self.assertEqual(self.ids(q="graph"), [self.b, self.c])  # tag substring
        self.assertEqual(self.ids(q="nothing-matches"), [])

    def test_tag_filter_is_exact(self):
        self.assertEqual(self.ids(tag="graphs"), [self.b, self.c])
        self.assertEqual(self.ids(tag="graph"), [])
        self.assertEqual(self.ids(tag="bfs"), [self.c])

    def test_status_filter(self):
        self.assertEqual(self.ids(status="new"), [self.a])
        self.assertEqual(self.ids(status="scheduled"), [self.b, self.c])
        self.assertEqual(self.ids(status="due"), [])
        with self.assertRaises(Invalid):
            self.ids(status="bogus")

    def test_combined_filters(self):
        self.assertEqual(self.ids(q="word", tag="graphs", status="scheduled"), [self.c])

    def test_tags_counts(self):
        self.assertEqual(self.store.tags(), [
            {"tag": "graphs", "count": 2},
            {"tag": "arrays", "count": 1},
            {"tag": "bfs", "count": 1},
            {"tag": "hash-map", "count": 1},
        ])

    def test_status_is_by_study_date(self):
        cases = ((ANCHOR, "due", 0, 0), (ANCHOR + DAY, "scheduled", 1, 0),
                 (ANCHOR - DAY, "due", -1, 1), (ANCHOR + 30 * DAY, "scheduled", 30, 0))
        for due, status, due_in, overdue in cases:
            with self.subTest(due=due):
                set_card(self.db_path, self.b, review_state_card(
                    last_review=ANCHOR - 2 * DAY, stability=1.0, due=due))
                p = self.store.get_problem(self.b)
                self.assertEqual((p["status"], p["due_in_days"], p["overdue_days"]),
                                 (status, due_in, overdue))
                d = sched.anchor_date(due)
                self.assertEqual(p["due_date"], d.isoformat())
                self.assertEqual(p["due"], day_start_utc(d))
                self.assertIn(self.b, [q["id"] for q in self.store.list_problems(status=status)])

    def test_legacy_non_anchored_due_uses_its_utc_date(self):
        set_card(self.db_path, self.b, review_state_card(
            last_review=ANCHOR - 2 * DAY, stability=1.0, due=utc(2026, 1, 15, 23, 59)))
        p = self.store.get_problem(self.b)
        self.assertEqual((p["due_date"], p["status"]), ("2026-01-15", "due"))

    def test_overdue_days(self):
        set_card(self.db_path, self.b, review_state_card(last_review=ANCHOR - 8 * DAY, stability=2.0,
                                                         due=ANCHOR - 3 * DAY))
        p = self.store.get_problem(self.b)
        self.assertEqual(p["status"], "due")
        self.assertEqual((p["overdue_days"], p["due_in_days"], p["due_date"]),
                         (3, -3, "2026-01-12"))
        self.assertIs(type(p["overdue_days"]), int)
        self.assertTrue(0 < p["retrievability"] < 0.9)


# ============================================================================ deck scope
class DeckScopeTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.main1 = self.add("Main One", tags=["arrays"])["id"]
        self.main2 = self.add("Main Two", tags=["graphs"])["id"]
        self.nc1 = self.add("NC One", tags=["arrays"], deck="neetcode")["id"]
        self.nc2 = self.add("NC Two", tags=["dp"], deck="neetcode")["id"]

    def ids(self, **kw):
        return {p["id"] for p in self.store.list_problems(**kw)}

    def test_default_scope_is_main_only(self):
        self.assertEqual(self.ids(), {self.main1, self.main2})

    def test_neetcode_scope(self):
        self.assertEqual(self.ids(scope="neetcode"), {self.nc1, self.nc2})

    def test_all_scope(self):
        self.assertEqual(self.ids(scope="all"), {self.main1, self.main2, self.nc1, self.nc2})

    def test_invalid_scope_raises(self):
        with self.assertRaises(Invalid):
            self.store.list_problems(scope="bogus")
        with self.assertRaises(Invalid):
            self.store.queue(scope="bogus")
        with self.assertRaises(Invalid):
            self.store.summary(scope="bogus")
        with self.assertRaises(Invalid):
            self.store.tags(scope="bogus")

    def test_toggle_adds_neetcode_to_main_scope(self):
        self.assertEqual(self.ids(), {self.main1, self.main2})
        self.store.update_settings({"neetcode_in_main": True})
        self.assertEqual(self.ids(), {self.main1, self.main2, self.nc1, self.nc2})
        # explicit neetcode/all scopes are unaffected by the toggle
        self.assertEqual(self.ids(scope="neetcode"), {self.nc1, self.nc2})
        self.assertEqual(self.ids(scope="all"), {self.main1, self.main2, self.nc1, self.nc2})

    def test_tags_respect_scope(self):
        self.assertEqual({t["tag"] for t in self.store.tags()}, {"arrays", "graphs"})
        self.assertEqual({t["tag"] for t in self.store.tags(scope="neetcode")}, {"arrays", "dp"})
        self.assertEqual({t["tag"] for t in self.store.tags(scope="all")}, {"arrays", "graphs", "dp"})
        self.store.update_settings({"neetcode_in_main": True})
        self.assertEqual({t["tag"] for t in self.store.tags()}, {"arrays", "graphs", "dp"})


# ============================================================================ queue
class QueueTest(FrozenStoreTestCase):
    def test_empty(self):
        self.assertEqual(self.store.queue(), {
            "due": [], "new": [], "new_waiting": 0, "new_left_today": 3,
            "new_introduced_today": 0})

    def test_due_sorted_by_lowest_retrievability(self):
        p1 = self.add("P1", first_rating=3)["id"]
        p2 = self.add("P2", first_rating=3)["id"]
        p3 = self.add("P3", first_rating=3)["id"]
        # due order would be p3, p1, p2; id order p1, p2, p3; recall order p2, p3, p1
        set_card(self.db_path, p1, review_state_card(last_review=ANCHOR - 3 * DAY, stability=3.0,
                                                     due=ANCHOR - 2 * DAY))
        set_card(self.db_path, p2, review_state_card(last_review=ANCHOR - 20 * DAY, stability=2.0,
                                                     due=ANCHOR))
        set_card(self.db_path, p3, review_state_card(last_review=ANCHOR - 10 * DAY, stability=5.0,
                                                     due=ANCHOR - 5 * DAY))
        self.add("Scheduled", first_rating=4)
        self.add("New")
        due = self.store.queue()["due"]
        self.assertEqual([p["id"] for p in due], [p2, p3, p1])
        rs = [p["retrievability"] for p in due]
        self.assertEqual(rs, sorted(rs))
        self.assertTrue(all(p["status"] == "due" for p in due))

    def test_ties_broken_by_most_overdue(self):
        a = self.add("A", first_rating=3)["id"]
        b = self.add("B", first_rating=3)["id"]
        set_card(self.db_path, a, review_state_card(last_review=ANCHOR - 4 * DAY, stability=4.0,
                                                    due=ANCHOR - DAY))
        set_card(self.db_path, b, review_state_card(last_review=ANCHOR - 4 * DAY, stability=4.0,
                                                    due=ANCHOR - 3 * DAY))
        self.assertEqual([p["id"] for p in self.store.queue()["due"]], [b, a])

    def test_new_per_day_limit(self):
        ids = [self.add(f"N{i}")["id"] for i in range(5)]
        q = self.store.queue()
        self.assertEqual([p["id"] for p in q["new"]], ids[:3])
        self.assertEqual((q["new_waiting"], q["new_left_today"], q["new_introduced_today"]),
                         (5, 3, 0))

    def test_problems_added_with_first_rating_do_not_count(self):
        ids = [self.add(f"N{i}")["id"] for i in range(5)]
        for i in range(4):
            self.add(f"Solved{i}", first_rating=(i % 4) + 1)
        q = self.store.queue()
        self.assertEqual((q["new_left_today"], q["new_introduced_today"]), (3, 0))
        self.assertEqual([p["id"] for p in q["new"]], ids[:3])

    def test_rereviewing_known_problems_does_not_count(self):
        pid = self.add("Solved", first_rating=3)["id"]
        self.store.review_problem(pid, 3)
        self.store.review_problem(pid, 1)
        self.assertEqual(self.store.queue()["new_introduced_today"], 0)

    def test_reviews_from_new_queue_count(self):
        ids = [self.add(f"N{i}")["id"] for i in range(5)]
        self.store.review_problem(ids[0], 3)
        q = self.store.queue()
        self.assertEqual((q["new_waiting"], q["new_left_today"], q["new_introduced_today"]),
                         (4, 2, 1))
        self.assertEqual([p["id"] for p in q["new"]], ids[1:3])
        # reviewing the same problem again today doesn't count twice
        self.store.review_problem(ids[0], 3)
        self.assertEqual(self.store.queue()["new_introduced_today"], 1)
        self.store.review_problem(ids[1], 1)
        self.store.review_problem(ids[2], 4)
        q = self.store.queue()
        self.assertEqual((q["new"], q["new_waiting"], q["new_left_today"],
                          q["new_introduced_today"]), ([], 2, 0, 3))
        # going over the limit (e.g. reviewing from the problem list) never goes negative
        self.store.review_problem(ids[3], 3)
        q = self.store.queue()
        self.assertEqual((q["new_left_today"], q["new_introduced_today"]), (0, 4))

    def test_undo_gives_the_new_slot_back(self):
        ids = [self.add(f"N{i}")["id"] for i in range(4)]
        self.store.review_problem(ids[0], 3)
        self.store.undo_last_review(ids[0])
        q = self.store.queue()
        self.assertEqual((q["new_left_today"], q["new_introduced_today"]), (3, 0))
        self.assertEqual([p["id"] for p in q["new"]], ids[:3])

    def test_introductions_counted_per_study_day(self):
        ids = [self.add(f"N{i}")["id"] for i in range(6)]
        start = self.day_start()
        insert_review(self.db_path, ids[0], start - timedelta(minutes=1), first=True)  # yesterday
        insert_review(self.db_path, ids[1], start - 2 * DAY, first=True)
        insert_review(self.db_path, ids[2], start, first=True)  # first instant of today
        self.assertEqual(self.store.queue()["new_introduced_today"], 1)
        # tomorrow the counter resets
        self.advance(days=1)
        self.assertEqual(self.store.queue()["new_introduced_today"], 0)

    def test_new_per_day_setting(self):
        for i in range(5):
            self.add(f"N{i}")
        self.store.update_settings({"new_per_day": 0})
        q = self.store.queue()
        self.assertEqual((q["new"], q["new_left_today"], q["new_waiting"]), ([], 0, 5))
        self.store.update_settings({"new_per_day": 10})
        self.assertEqual(len(self.store.queue()["new"]), 5)

    def test_lowering_limit_after_introductions(self):
        ids = [self.add(f"N{i}")["id"] for i in range(5)]
        self.store.review_problem(ids[0], 3)
        self.store.review_problem(ids[1], 3)
        self.store.update_settings({"new_per_day": 1})
        q = self.store.queue()
        self.assertEqual((q["new"], q["new_left_today"]), ([], 0))

    def test_tag_filter(self):
        g = self.add("G", tags=["graphs"])["id"]
        self.add("A", tags=["arrays"])
        gd = self.add("GD", tags=["graphs"], first_rating=3)["id"]
        ad = self.add("AD", tags=["arrays"], first_rating=3)["id"]
        for pid in (gd, ad):
            set_card(self.db_path, pid, review_state_card(last_review=ANCHOR - 5 * DAY,
                                                          stability=2.0, due=ANCHOR - DAY))
        q = self.store.queue(tag="graphs")
        self.assertEqual([p["id"] for p in q["due"]], [gd])
        self.assertEqual([p["id"] for p in q["new"]], [g])
        self.assertEqual(q["new_waiting"], 1)
        self.assertEqual(len(self.store.queue()["due"]), 2)

    # -------------------------------------------------------------- deck-aware queue
    def test_neetcode_scope_uses_its_own_limit(self):
        main_ids = [self.add(f"M{i}")["id"] for i in range(4)]
        nc_ids = [self.add(f"N{i}", deck="neetcode")["id"] for i in range(5)]
        self.store.update_settings({"neetcode_new_per_day": 2})
        q = self.store.queue(scope="neetcode")
        self.assertEqual([p["id"] for p in q["new"]], nc_ids[:2])
        self.assertEqual((q["new_waiting"], q["new_left_today"], q["new_introduced_today"]), (5, 2, 0))
        # main-scope queue is unaffected
        q_main = self.store.queue()
        self.assertEqual([p["id"] for p in q_main["new"]], main_ids[:3])

    def test_introducing_neetcode_new_does_not_touch_main_limit(self):
        main_ids = [self.add(f"M{i}")["id"] for i in range(4)]
        nc_ids = [self.add(f"N{i}", deck="neetcode")["id"] for i in range(4)]
        self.store.review_problem(nc_ids[0], 3)
        self.assertEqual(self.store.queue()["new_introduced_today"], 0)
        self.assertEqual([p["id"] for p in self.store.queue()["new"]], main_ids[:3])
        self.assertEqual(self.store.queue(scope="neetcode")["new_introduced_today"], 1)

    def test_introducing_main_new_does_not_touch_neetcode_limit(self):
        main_ids = [self.add(f"M{i}")["id"] for i in range(4)]
        self.add("N0", deck="neetcode")
        self.store.review_problem(main_ids[0], 3)
        self.assertEqual(self.store.queue()["new_introduced_today"], 1)
        self.assertEqual(self.store.queue(scope="neetcode")["new_introduced_today"], 0)

    def test_moving_introduced_problems_to_another_deck_does_not_reset_the_limit(self):
        # Regression test: whether a review counts against a deck's daily "new" limit
        # must be decided by the deck the problem was in AT THE TIME of that review, not
        # whichever deck it happens to be in now - otherwise moving problems to another
        # deck (e.g. "Move to NeetCode") resets or bypasses the limit that introduced them.
        ids = [self.add(f"M{i}")["id"] for i in range(3)]  # default new_per_day is 3
        for pid in ids:
            self.store.review_problem(pid, 3)
        self.assertEqual(self.store.queue()["new_left_today"], 0)
        for pid in ids:
            self.store.update_problem(pid, {"deck": "neetcode"})
        # still 0 - those three reviews still count against the main deck's limit today,
        # even though the problems themselves are no longer in the main deck
        q = self.store.queue()
        self.assertEqual((q["new_left_today"], q["new_introduced_today"]), (0, 3))
        # and they don't ALSO eat into the neetcode deck's own, separate limit
        self.assertEqual(self.store.queue(scope="neetcode")["new_introduced_today"], 0)

    def test_combined_queue_with_toggle_on_orders_main_then_neetcode(self):
        main_ids = [self.add(f"M{i}")["id"] for i in range(2)]
        nc_ids = [self.add(f"N{i}", deck="neetcode")["id"] for i in range(2)]
        self.store.update_settings({"neetcode_in_main": True, "new_per_day": 1,
                                    "neetcode_new_per_day": 1})
        q = self.store.queue()
        self.assertEqual([p["id"] for p in q["new"]], [main_ids[0], nc_ids[0]])
        self.assertEqual((q["new_waiting"], q["new_left_today"]), (4, 2))

    def test_combined_queue_due_sorted_together(self):
        m = self.add("M", first_rating=3)["id"]
        n = self.add("N", deck="neetcode", first_rating=3)["id"]
        set_card(self.db_path, m, review_state_card(last_review=ANCHOR - 20 * DAY, stability=2.0,
                                                     due=ANCHOR - DAY))
        set_card(self.db_path, n, review_state_card(last_review=ANCHOR - 3 * DAY, stability=3.0,
                                                     due=ANCHOR - 2 * DAY))
        self.store.update_settings({"neetcode_in_main": True})
        due_ids = [p["id"] for p in self.store.queue()["due"]]
        self.assertEqual(set(due_ids), {m, n})
        # weakest recall first, across both decks
        rs = [p["retrievability"] for p in self.store.queue()["due"]]
        self.assertEqual(rs, sorted(rs))

    def test_all_scope_behaves_like_main_with_toggle_on(self):
        main_ids = [self.add(f"M{i}")["id"] for i in range(2)]
        nc_ids = [self.add(f"N{i}", deck="neetcode")["id"] for i in range(2)]
        q_all = self.store.queue(scope="all")
        self.assertEqual({p["id"] for p in q_all["new"]}, {*main_ids, *nc_ids})


# ============================================================================ day math
class DayBoundsTest(StoreTestCase):
    def test_properties_for_every_start_hour(self):
        now = sched.utcnow()
        for hour in range(24):
            with self.subTest(hour=hour):
                start, end = self.store.day_bounds(now, {"day_starts_at": hour})
                self.assertIs(start.tzinfo, timezone.utc)
                self.assertIs(end.tzinfo, timezone.utc)
                self.assertTrue(start <= now < end)
                self.assertEqual(end - start, DAY)
                local_start = start.astimezone(now.astimezone().tzinfo)
                self.assertEqual((local_start.hour, local_start.minute, local_start.second,
                                  local_start.microsecond), (hour, 0, 0, 0))

    def test_before_start_hour_belongs_to_previous_day(self):
        start, end = self.store.day_bounds(local(2026, 1, 15, 3, 30), {"day_starts_at": 4})
        self.assertEqual(start, local(2026, 1, 14, 4))
        self.assertEqual(end, local(2026, 1, 15, 4))

    def test_after_start_hour_belongs_to_same_day(self):
        start, end = self.store.day_bounds(local(2026, 1, 15, 4, 0, 1), {"day_starts_at": 4})
        self.assertEqual(start, local(2026, 1, 15, 4))
        self.assertEqual(end, local(2026, 1, 16, 4))

    def test_exactly_at_start_hour(self):
        start, _ = self.store.day_bounds(local(2026, 1, 15, 4), {"day_starts_at": 4})
        self.assertEqual(start, local(2026, 1, 15, 4))

    def test_midnight_start(self):
        start, end = self.store.day_bounds(local(2026, 1, 15, 23, 59, 59), {"day_starts_at": 0})
        self.assertEqual((start, end), (local(2026, 1, 15), local(2026, 1, 16)))
        start, _ = self.store.day_bounds(local(2026, 1, 16, 0, 0, 1), {"day_starts_at": 0})
        self.assertEqual(start, local(2026, 1, 16))

    def test_late_start_hour(self):
        start, _ = self.store.day_bounds(local(2026, 1, 15, 22, 59), {"day_starts_at": 23})
        self.assertEqual(start, local(2026, 1, 14, 23))
        start, _ = self.store.day_bounds(local(2026, 1, 15, 23, 1), {"day_starts_at": 23})
        self.assertEqual(start, local(2026, 1, 15, 23))

    def test_uses_saved_setting_by_default(self):
        self.store.update_settings({"day_starts_at": 7})
        start, _ = self.store.day_bounds(local(2026, 1, 15, 6, 59))
        self.assertEqual(start, local(2026, 1, 14, 7))
        start, _ = self.store.day_bounds(local(2026, 1, 15, 7, 0))
        self.assertEqual(start, local(2026, 1, 15, 7))

    def test_defaults_to_now(self):
        start, end = self.store.day_bounds()
        self.assertTrue(start <= sched.utcnow() < end)


# ============================================================================ summary
class SummaryTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.pid = self.add("Streaky")["id"]

    def review_days_ago(self, *days_ago):
        start = self.day_start()
        for d in days_ago:
            when = NOW if d == 0 else start - d * DAY + timedelta(hours=1)
            insert_review(self.db_path, self.pid, when)

    def test_empty_shape(self):
        s = self.store.summary()
        self.assertEqual(set(s), {"now", "day_start", "day_end", "today", "counts", "streak_days",
                                  "recall_rate_30d", "reviews_30d", "forecast", "settings",
                                  "deck_counts"})
        self.assertEqual(s["now"], NOW.isoformat())
        self.assertEqual(s["today"], TODAY.isoformat())
        start, end = self.store.day_bounds(NOW)
        self.assertEqual((s["day_start"], s["day_end"]), (start.isoformat(), end.isoformat()))
        self.assertEqual(s["counts"], {"total": 1, "active": 1, "suspended": 0, "new": 1,
                                       "due": 0, "scheduled": 0, "reviewed_today": 0})
        self.assertEqual((s["streak_days"], s["recall_rate_30d"], s["reviews_30d"]),
                         (0, None, 0))
        self.assertEqual(s["settings"], DEFAULT_SETTINGS)
        self.assertEqual(len(s["forecast"]), 14)
        self.assertEqual({f["count"] for f in s["forecast"]}, {0})

    def test_counts_and_forecast(self):
        self.add("New 2")
        sched1 = self.add("Good today", first_rating=3)["id"]           # due in 2 days

        def add_due(title, due, **kw):
            pid = self.add(title, first_rating=3, **kw)["id"]
            set_card(self.db_path, pid, review_state_card(last_review=ANCHOR - 6 * DAY,
                                                          stability=2.0, due=due))
            return pid

        add_due("Overdue", ANCHOR - 3 * DAY)
        add_due("Due today", ANCHOR)
        add_due("Suspended", ANCHOR - 3 * DAY, suspended=True)
        self.add("Suspended new", suspended=True)
        far = add_due("Far", ANCHOR + 14 * DAY)
        add_due("Day 5", ANCHOR + 5 * DAY)
        add_due("Day 13", ANCHOR + 13 * DAY)
        s = self.store.summary()
        self.assertEqual(s["counts"], {"total": 10, "active": 8, "suspended": 2, "new": 2,
                                       "due": 2, "scheduled": 4, "reviewed_today": 7})
        counts = [f["count"] for f in s["forecast"]]
        self.assertEqual(counts, [2, 0, 1, 0, 0, 1] + [0] * 7 + [1])
        self.assertEqual(s["forecast"][2]["date"], "2026-01-17")
        p = self.store.get_problem(sched1)
        self.assertEqual((p["due_date"], p["due_in_days"]), ("2026-01-17", 2))
        self.assertEqual(p["due"], day_start_utc(date(2026, 1, 17)))
        self.assertEqual(self.store.get_problem(far)["due_in_days"], 14)

    def test_forecast_dates_are_consecutive_study_days(self):
        s = self.store.summary()
        dates = [date.fromisoformat(f["date"]) for f in s["forecast"]]
        self.assertEqual(dates[0], TODAY)
        self.assertEqual(s["forecast"][0]["date"], s["today"])
        for a, b in zip(dates, dates[1:]):
            self.assertEqual(b - a, timedelta(days=1))

    def test_reviewed_today_uses_study_day(self):
        start = self.day_start()
        insert_review(self.db_path, self.pid, start - timedelta(seconds=1))
        insert_review(self.db_path, self.pid, start)
        insert_review(self.db_path, self.pid, NOW, kind="added")
        self.assertEqual(self.store.summary()["counts"]["reviewed_today"], 2)

    def test_streak_zero_without_reviews(self):
        self.assertEqual(self.store.summary()["streak_days"], 0)

    def test_streak_today_only(self):
        self.review_days_ago(0)
        self.assertEqual(self.store.summary()["streak_days"], 1)

    def test_streak_including_today(self):
        self.review_days_ago(0, 1, 2)
        self.assertEqual(self.store.summary()["streak_days"], 3)

    def test_today_not_yet_reviewed_does_not_break_streak(self):
        self.review_days_ago(1, 2, 3)
        self.assertEqual(self.store.summary()["streak_days"], 3)

    def test_gap_breaks_streak(self):
        self.review_days_ago(0, 2, 3)
        self.assertEqual(self.store.summary()["streak_days"], 1)

    def test_missed_yesterday_and_today_is_zero(self):
        self.review_days_ago(2, 3, 4)
        self.assertEqual(self.store.summary()["streak_days"], 0)

    def test_many_reviews_same_day_count_once(self):
        self.review_days_ago(1, 1, 1, 0, 0)
        self.assertEqual(self.store.summary()["streak_days"], 2)

    def test_streak_uses_study_day_boundary(self):
        # A review at 03:59 (day starts at 04:00) belongs to the previous study day.
        insert_review(self.db_path, self.pid, self.day_start() - timedelta(minutes=1))
        s = self.store.summary()
        self.assertEqual((s["streak_days"], s["counts"]["reviewed_today"]), (1, 0))

    def test_streak_with_day_starting_at_midnight(self):
        self.store.update_settings({"day_starts_at": 0})
        self.review_days_ago(1, 2)
        self.assertEqual(self.store.summary()["streak_days"], 2)

    # Regression test for a fixed bug (store.Store.summary): `today` is computed as (day_start + 1 hour).date(), while
    # review days are labelled (reviewed_at - day_starts_at hours).date(). With
    # day_starts_at=23 the "+1 hour" rolls today's label onto the next calendar date, so
    # it never matches: a streak of yesterday + the day before shows 0 until you review
    # today (expected 2). Using day_start.astimezone(local_tz).date() fixes it.
    def test_streak_with_day_starting_at_23(self):
        self.store.update_settings({"day_starts_at": 23})
        self.review_days_ago(1, 2)
        self.assertEqual(self.store.summary()["streak_days"], 2)

    def test_recall_rate_excludes_first_reviews(self):
        a = self.pid
        b = self.add("B")["id"]
        c = self.add("C")["id"]
        insert_review(self.db_path, a, NOW - 10 * DAY, 3, first=True)   # first -> excluded
        insert_review(self.db_path, a, NOW - 5 * DAY, 1)                # fail
        insert_review(self.db_path, a, NOW - 2 * DAY, 3)                # pass
        insert_review(self.db_path, b, NOW - 40 * DAY, 3, first=True)   # too old
        insert_review(self.db_path, b, NOW - 35 * DAY, 1)               # too old
        insert_review(self.db_path, b, NOW - DAY, 4)                    # pass (not first)
        insert_review(self.db_path, c, NOW - DAY, 1, first=True, kind="added")  # first
        s = self.store.summary()
        self.assertEqual(s["reviews_30d"], 3)
        self.assertEqual(s["recall_rate_30d"], 0.667)

    def test_recall_rate_none_when_only_first_reviews(self):
        self.add("X", first_rating=1)
        self.store.review_problem(self.pid, 3)
        s = self.store.summary()
        self.assertEqual((s["recall_rate_30d"], s["reviews_30d"]), (None, 0))
        self.store.review_problem(self.pid, 2)  # second review of the same problem
        s = self.store.summary()
        self.assertEqual((s["recall_rate_30d"], s["reviews_30d"]), (1.0, 1))

    def test_recall_rate_all_again(self):
        self.store.review_problem(self.pid, 3)
        self.store.review_problem(self.pid, 1)
        self.store.review_problem(self.pid, 1)
        self.assertEqual(self.store.summary()["recall_rate_30d"], 0.0)

    # -------------------------------------------------------------- deck scope / deck_counts
    def test_deck_counts_always_covers_both_decks(self):
        # self.pid (from setUp) is a "new" main-deck problem.
        nc = self.add("NC due", deck="neetcode", first_rating=3)["id"]
        set_card(self.db_path, nc, review_state_card(last_review=ANCHOR - 5 * DAY, stability=2.0,
                                                      due=ANCHOR - DAY))
        self.add("NC new", deck="neetcode")
        for scope in ("main", "neetcode", "all"):
            s = self.store.summary(scope=scope)
            self.assertEqual(s["deck_counts"], {
                "main": {"total": 1, "due": 0, "new": 1},
                "neetcode": {"total": 2, "due": 1, "new": 1},
            })

    def test_deck_counts_excludes_suspended(self):
        self.add("NC suspended", deck="neetcode", suspended=True)
        self.assertEqual(self.store.summary()["deck_counts"]["neetcode"],
                         {"total": 0, "due": 0, "new": 0})

    def test_summary_scope_main_excludes_neetcode_by_default(self):
        self.add("NC", deck="neetcode", first_rating=3)
        s = self.store.summary()
        self.assertEqual(s["counts"]["total"], 1)  # only self.pid

    def test_summary_scope_neetcode(self):
        self.add("NC1", deck="neetcode")
        self.add("NC2", deck="neetcode")
        s = self.store.summary(scope="neetcode")
        self.assertEqual(s["counts"]["total"], 2)

    def test_summary_scope_all(self):
        self.add("NC", deck="neetcode")
        s = self.store.summary(scope="all")
        self.assertEqual(s["counts"]["total"], 2)  # self.pid + NC

    def test_summary_toggle_includes_neetcode_in_main_scope(self):
        self.add("NC", deck="neetcode")
        self.store.update_settings({"neetcode_in_main": True})
        self.assertEqual(self.store.summary()["counts"]["total"], 2)

    def test_reviewed_today_respects_scope(self):
        nc = self.add("NC", deck="neetcode")["id"]
        self.store.review_problem(nc, 3)
        self.assertEqual(self.store.summary()["counts"]["reviewed_today"], 0)
        self.assertEqual(self.store.summary(scope="neetcode")["counts"]["reviewed_today"], 1)
        self.assertEqual(self.store.summary(scope="all")["counts"]["reviewed_today"], 1)


# ============================================================================ settings
class SettingsTest(FrozenStoreTestCase):
    def test_defaults(self):
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)
        ss = self.store.scheduler_settings()
        self.assertEqual((ss.desired_retention, ss.maximum_interval, ss.again_next_day),
                         (0.9, 60, True))

    def test_update_persists_and_returns_all(self):
        new = self.store.update_settings({"desired_retention": 0.85, "new_per_day": 5})
        expected = dict(DEFAULT_SETTINGS, desired_retention=0.85, new_per_day=5)
        self.assertEqual(new, expected)
        self.assertEqual(Store(self.db_path).get_settings(), expected)

    def test_values_are_coerced(self):
        new = self.store.update_settings({"desired_retention": "0.8512", "maximum_interval": "30",
                                          "new_per_day": 4.0, "day_starts_at": "6",
                                          "again_next_day": 0})
        self.assertEqual(new, {"desired_retention": 0.851, "maximum_interval": 30,
                               "new_per_day": 4, "day_starts_at": 6, "again_next_day": False,
                               "fsrs_parameters": None, "neetcode_in_main": False,
                               "neetcode_new_per_day": 3, "allow_code_run": True,
                               "claude_mode": "off", "claude_model": "sonnet"})
        self.assertIsInstance(new["maximum_interval"], int)
        self.assertIsInstance(new["again_next_day"], bool)

    def test_strings_are_not_booleans(self):
        for value in ("false", "true", "", 2, 0.5, None):
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.store.update_settings({"again_next_day": value})

    def test_custom_fsrs_parameters(self):
        import scheduling
        from fsrs.scheduler import DEFAULT_PARAMETERS
        pid = self.add("Params", first_rating=3)["id"]
        before = self.store.get_problem(pid)["due"]
        params = list(DEFAULT_PARAMETERS)
        params[2] = 10.0   # much higher initial stability for "Good"
        new = self.store.update_settings({"fsrs_parameters": params})
        self.assertEqual(new["fsrs_parameters"], params)
        self.assertGreater(self.store.get_problem(pid)["due"], before)
        self.assertEqual(self.store.scheduler_settings().parameters, tuple(params))
        for bad in ([1.0] * 3, ["x"] * 21, [float("inf")] + params[1:], [100.0] * 21, "abc"):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.update_settings({"fsrs_parameters": bad})
        self.assertIsNone(self.store.update_settings({"fsrs_parameters": None})["fsrs_parameters"])
        self.assertEqual(self.store.get_problem(pid)["due"], before)   # 2-day interval has no fuzz

    def test_undo_after_settings_change_matches_new_settings(self):
        pid = self.add("Undo", first_rating=3)["id"]
        self.store.review_problem(pid, 3)
        self.store.update_settings({"desired_retention": 0.97})
        p = self.store.undo_last_review(pid)
        # After undo, the schedule equals a fresh replay of the remaining history under 0.97.
        fresh = self.store.reschedule_all(self.store.scheduler_settings())
        self.assertEqual(fresh, 1)
        self.assertEqual(self.store.get_problem(pid)["due"], p["due"])

    def test_boundaries_accepted(self):
        for key, values in {"desired_retention": (0.70, 0.99), "maximum_interval": (1, 36500),
                            "new_per_day": (0, 100), "day_starts_at": (0, 23)}.items():
            for v in values:
                with self.subTest(key=key, value=v):
                    self.assertEqual(self.store.update_settings({key: v})[key], v)

    def test_out_of_range_rejected(self):
        for key, values in {"desired_retention": (0.69, 0.6949, 0.991, 1.0, 0, -0.9, "nan"),
                            "maximum_interval": (0, -1, 36501),
                            "new_per_day": (-1, 101),
                            "day_starts_at": (-1, 24, 100)}.items():
            for v in values:
                with self.subTest(key=key, value=v), self.assertRaises(Invalid):
                    self.store.update_settings({key: v})
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    def test_non_numeric_rejected(self):
        for key in ("desired_retention", "maximum_interval", "new_per_day", "day_starts_at"):
            for v in ("abc", None, [], {}):
                with self.subTest(key=key, value=v), self.assertRaises(Invalid):
                    self.store.update_settings({key: v})
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    def test_unknown_key_rejected(self):
        with self.assertRaises(Invalid) as ctx:
            self.store.update_settings({"new_per_day": 5, "learning_steps": [1]})
        self.assertIn("unknown setting", str(ctx.exception))
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    def test_invalid_patch_is_all_or_nothing(self):
        with self.assertRaises(Invalid):
            self.store.update_settings({"new_per_day": 5, "desired_retention": 2})
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    # Regression test for a fixed bug (store.update_settings): int(float('inf')) raises OverflowError, which isn't
    # caught, so PATCH /api/settings {"maximum_interval": 1e400} is a 500, not a 400.
    def test_infinite_values_rejected_cleanly(self):
        with self.assertRaises(Invalid):
            self.store.update_settings({"maximum_interval": float("inf")})

    def test_unrelated_rows_ignored(self):
        with connect(self.db_path) as c:
            c.execute("INSERT INTO settings(key, value) VALUES ('legacy', '1')")
        self.assertEqual(self.store.get_settings(), DEFAULT_SETTINGS)

    def gap(self, pid):
        card = get_card(self.db_path, pid)
        return card.due - card.last_review

    def assert_snapshot_chain(self, pid):
        """Each review's before/after snapshots line up and match the problem's card."""
        with connect(self.db_path) as c:
            rows = c.execute("SELECT card_before, card_after FROM reviews WHERE problem_id = ? "
                             "ORDER BY reviewed_at, id", (pid,)).fetchall()
        self.assertIsNone(json.loads(rows[0]["card_before"])["last_review"])
        for prev, row in zip(rows, rows[1:]):
            self.assertEqual(json.loads(row["card_before"]), json.loads(prev["card_after"]))
        self.assertEqual(json.loads(rows[-1]["card_after"]),
                         json.loads(problem_row(self.db_path, pid)["card"]))
        for row in rows:
            after = sched.card_from_json(row["card_after"])
            self.assertTrue(sched.is_anchored(after.last_review))
            self.assertTrue(sched.is_anchored(after.due))
            self.assertEqual((after.due - after.last_review) % DAY, timedelta(0))

    def test_changing_desired_retention_reschedules(self):
        easy = self.add("Easy", first_rating=4)["id"]
        good = self.add("Good", first_rating=3)["id"]
        new = self.add("New")["id"]
        new_before = problem_row(self.db_path, new)
        self.store.update_settings({"desired_retention": 0.99})
        self.assertEqual(self.gap(easy), DAY)
        self.assertEqual(self.gap(good), DAY)
        self.assertEqual(get_card(self.db_path, easy).last_review, ANCHOR)
        p = self.store.get_problem(easy)
        self.assertEqual((p["due_date"], p["due_in_days"], p["status"]),
                         ("2026-01-16", 1, "scheduled"))
        self.assertEqual(p["due"], day_start_utc(date(2026, 1, 16)))
        self.assertEqual(p["last_review"], NOW.isoformat())
        self.assertEqual(problem_row(self.db_path, easy)["due"], (ANCHOR + DAY).isoformat())
        self.assertEqual(problem_row(self.db_path, new), new_before)
        self.store.update_settings({"desired_retention": 0.70})
        # 77 days, capped at 60, then (seeded) fuzz
        self.assertTrue(55 <= self.gap(easy).days <= 60, self.gap(easy))
        self.assertEqual(self.gap(easy) % DAY, timedelta(0))
        # history is kept; only the card snapshots are recomputed
        self.assertEqual(count_rows(self.db_path, "reviews"), 2)
        self.assert_snapshot_chain(easy)

    def test_changing_maximum_interval_reschedules(self):
        easy = self.add("Easy", first_rating=4)["id"]
        self.store.update_settings({"maximum_interval": 3})
        self.assertTrue(2 <= self.gap(easy).days <= 3, self.gap(easy))
        self.store.update_settings({"maximum_interval": 36500})
        self.assertTrue(6 <= self.gap(easy).days <= 11, self.gap(easy))  # 8 days +/- fuzz
        long_card = problem_row(self.db_path, easy)["card"]
        # the same settings always give the same (seeded) schedule
        self.store.update_settings({"maximum_interval": 3})
        self.store.update_settings({"maximum_interval": 36500})
        self.assertEqual(problem_row(self.db_path, easy)["card"], long_card)

    def test_rescheduling_replays_full_history(self):
        pid = self.add("History")["id"]
        times = [NOW - 60 * DAY, NOW - 58 * DAY, NOW - 47 * DAY, NOW]
        ratings = (3, 3, 3, 1)
        for t, rating in zip(times, ratings):
            insert_review(self.db_path, pid, t, rating, anchored=False)
        logs = [sched.ReviewLog(card_id=pid, rating=sched.Rating(r), review_datetime=t,
                                review_duration=None)
                for t, r in zip(times, ratings)]

        self.store.update_settings({"again_next_day": False})
        card = get_card(self.db_path, pid)
        expected = sched.reschedule(pid, logs, sched.SchedulerSettings(again_next_day=False))
        # same memory state as a replay of the history; fuzz only moves the due date
        self.assertEqual((card.stability, card.difficulty), (expected.stability, expected.difficulty))
        self.assertEqual(card.last_review, ANCHOR)
        self.assertGreaterEqual(self.gap(pid), 2 * DAY)
        self.assertEqual(self.store.get_problem(pid)["status"], "scheduled")
        self.assert_snapshot_chain(pid)

        self.store.update_settings({"again_next_day": True})
        self.assertEqual(self.gap(pid), DAY)
        self.assertEqual(get_card(self.db_path, pid).stability, expected.stability)
        self.assert_snapshot_chain(pid)
        # the real review timestamps are never changed
        with connect(self.db_path) as c:
            stored = [r[0] for r in c.execute("SELECT reviewed_at FROM reviews ORDER BY id")]
        self.assertEqual(stored, [t.isoformat() for t in times])

    def test_changing_day_starts_at_reschedules(self):
        pid = self.add("Night owl")["id"]
        insert_review(self.db_path, pid, local(2026, 1, 15, 2, 30), 3)  # before 4am: Jan 14
        self.store.reschedule_all(self.store.scheduler_settings())
        card = get_card(self.db_path, pid)
        self.assertEqual((card.last_review, card.due), (ANCHOR - DAY, ANCHOR + DAY))
        p = self.store.get_problem(pid)
        self.assertEqual((p["due_in_days"], p["due"]), (1, day_start_utc(date(2026, 1, 16))))

        self.store.update_settings({"day_starts_at": 2})
        card = get_card(self.db_path, pid)
        self.assertEqual((card.last_review, card.due), (ANCHOR, ANCHOR + 2 * DAY))
        p = self.store.get_problem(pid)
        self.assertEqual((p["due_in_days"], p["due"]), (2, day_start_utc(date(2026, 1, 17), 2)))
        self.assert_snapshot_chain(pid)

        self.store.update_settings({"day_starts_at": 4})
        self.assertEqual(get_card(self.db_path, pid).last_review, ANCHOR - DAY)

    def test_reschedule_is_deterministic(self):
        self.addCleanup(random.setstate, random.getstate())
        for i in range(6):
            pid = self.add(f"P{i}")["id"]
            for k, days_ago in enumerate((40, 30, 12)):
                insert_review(self.db_path, pid, NOW - days_ago * DAY - timedelta(hours=i),
                              4 if k == 0 else 3)
        random.seed(1)
        self.store.reschedule_all(self.store.scheduler_settings())
        first = snapshot(self.db_path)
        random.seed(2)
        self.store.reschedule_all(self.store.scheduler_settings())
        self.assertEqual(snapshot(self.db_path), first)
        Store(self.db_path).reschedule_all(Store(self.db_path).scheduler_settings())
        self.assertEqual(snapshot(self.db_path), first)
        # same history (same study days) but fuzz still spreads the problems out
        dues = {row[2] for row in first["problems"]}
        self.assertGreater(len(dues), 1)

    def test_non_scheduling_settings_do_not_reschedule(self):
        pid = self.add("Fuzzed", first_rating=4)["id"]
        before = snapshot(self.db_path)
        self.store.update_settings({"new_per_day": 10})
        self.store.update_settings({"desired_retention": 0.9, "maximum_interval": 60,
                                    "again_next_day": True, "day_starts_at": 4,
                                    "fsrs_parameters": None})  # same values
        self.assertEqual(snapshot(self.db_path), before)
        self.assertEqual(self.store.get_problem(pid)["reps"], 1)

    def test_reschedule_all_counts_reviewed_problems(self):
        self.add("New")
        self.add("A", first_rating=3)
        self.add("B", first_rating=1)
        self.assertEqual(self.store.reschedule_all(sched.SchedulerSettings()), 2)

    # -------------------------------------------------------------- neetcode settings
    def test_neetcode_settings_defaults(self):
        s = self.store.get_settings()
        self.assertEqual((s["neetcode_in_main"], s["neetcode_new_per_day"]), (False, 3))

    def test_neetcode_settings_validated(self):
        new = self.store.update_settings({"neetcode_in_main": True, "neetcode_new_per_day": 7})
        self.assertEqual((new["neetcode_in_main"], new["neetcode_new_per_day"]), (True, 7))
        self.assertIsInstance(new["neetcode_in_main"], bool)
        for bad in ("yes", 2, None):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.update_settings({"neetcode_in_main": bad})
        for bad in (-1, 101, "abc", None):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                self.store.update_settings({"neetcode_new_per_day": bad})
        for v in (0, 100):
            self.assertEqual(self.store.update_settings({"neetcode_new_per_day": v})["neetcode_new_per_day"], v)

    def test_neetcode_settings_do_not_reschedule(self):
        pid = self.add("Fuzzed", first_rating=4)["id"]
        before = snapshot(self.db_path)
        self.store.update_settings({"neetcode_in_main": True, "neetcode_new_per_day": 9})
        self.assertEqual(snapshot(self.db_path), before)
        self.assertEqual(self.store.get_problem(pid)["reps"], 1)

    def test_neetcode_settings_do_not_call_reschedule_all(self):
        with mock.patch.object(Store, "_reschedule_all") as m:
            self.store.update_settings({"neetcode_in_main": True, "neetcode_new_per_day": 9})
        m.assert_not_called()


# ============================================================================ export / import
class ExportImportTest(FrozenStoreTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.add("Two Sum", first_rating=3, url="https://x.example", tags=["arrays"],
                          notes="  n  ", solution="code", difficulty="Easy", language="go",
                          source="LC", prompt="p", insight="i")["id"]
        self.advance(days=2)
        self.store.review_problem(self.a, 1, 1200)
        self.advance(days=1)
        self.store.review_problem(self.a, 4)
        self.b = self.add("New one", tags=["graphs", "bfs"])["id"]
        self.c = self.add("Suspended", first_rating=2, suspended=True)["id"]
        self.store.update_settings({"new_per_day": 7})

        self.other_tmp = self.tmp / "other"
        self.other = Store(self.other_tmp / "fresh.db")

    def test_export_shape(self):
        data = self.store.export_all()
        json.dumps(data)  # must be JSON-serialisable
        self.assertEqual(data["app"], "dsa-review")
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["exported_at"], self.clock.return_value.isoformat())
        self.assertEqual(data["settings"]["new_per_day"], 7)
        self.assertEqual([p["id"] for p in data["problems"]], [self.a, self.b, self.c])
        p = data["problems"][0]
        self.assertIsInstance(p["card"], dict)
        self.assertEqual(p["tags"], ["arrays"])
        self.assertIs(data["problems"][2]["suspended"], True)
        self.assertEqual(len(data["reviews"]), 4)
        for r in data["reviews"]:
            self.assertNotIn("id", r)
            self.assertNotIn("problem_id", r)
            self.assertIn(r["problem_uid"], {q["uid"] for q in data["problems"]})
            self.assertIsInstance(r["card_before"], dict)
            self.assertIsInstance(r["card_after"], dict)
        self.assertEqual([r["kind"] for r in data["reviews"]],
                         ["added", "review", "review", "added"])

    def _strip(self, card):
        return {k: v for k, v in card.items() if k != "card_id"}

    def test_round_trip_into_fresh_db(self):
        unrelated = self.other.create_problem({"title": "Already here"})["id"]
        data = json.loads(json.dumps(self.store.export_all()))
        result = self.other.import_all(data)
        self.assertEqual(result, {"added": 3, "skipped": 0})

        src = {p["uid"]: p for p in self.store.list_problems()}
        dst = {p["uid"]: p for p in self.other.list_problems() if p["id"] != unrelated}
        self.assertEqual(set(src), set(dst))
        compare = ("title", "url", "source", "difficulty", "tags", "prompt", "insight", "notes",
                   "solution", "language", "suspended", "deck", "created_at", "updated_at", "status",
                   "due", "last_review", "stability", "fsrs_difficulty", "retrievability",
                   "reps", "lapses", "last_rating")
        for uid, s in src.items():
            d = dst[uid]
            self.assertNotEqual(d["id"], s["id"])  # fresh ids
            for key in compare:
                self.assertEqual(d[key], s[key], key)
            s_card = json.loads(problem_row(self.db_path, s["id"])["card"])
            d_card = json.loads(problem_row(self.other.db_path, d["id"])["card"])
            self.assertEqual(self._strip(d_card), self._strip(s_card))
            self.assertEqual(d_card["card_id"], d["id"])
            sh = self.store.get_problem(s["id"])["history"]
            dh = self.other.get_problem(d["id"])["history"]
            self.assertEqual([{k: v for k, v in h.items() if k != "id"} for h in dh],
                             [{k: v for k, v in h.items() if k != "id"} for h in sh])

        with connect(self.db_path) as c:
            src_reviews = [dict(r) for r in c.execute("SELECT * FROM reviews ORDER BY id")]
        with connect(self.other.db_path) as c:
            dst_reviews = [dict(r) for r in c.execute("SELECT * FROM reviews ORDER BY id")]
        self.assertEqual(len(dst_reviews), len(src_reviews))
        for s, d in zip(src_reviews, dst_reviews):
            for key in ("rating", "reviewed_at", "duration_ms", "kind"):
                self.assertEqual(d[key], s[key])
            for key in ("card_before", "card_after"):
                self.assertEqual(self._strip(json.loads(d[key])), self._strip(json.loads(s[key])))
                self.assertEqual(json.loads(d[key])["card_id"], d["problem_id"])

        # undo still works on imported data and restores the exported previous card
        new_a = dst[self.store.get_problem(self.a)["uid"]]["id"]
        self.other.undo_last_review(new_a)
        self.store.undo_last_review(self.a)
        self.assertEqual(
            self._strip(json.loads(problem_row(self.other.db_path, new_a)["card"])),
            self._strip(json.loads(problem_row(self.db_path, self.a)["card"])))

    def test_second_import_skips_everything(self):
        data = self.store.export_all()
        self.assertEqual(self.other.import_all(data), {"added": 3, "skipped": 0})
        n_reviews = count_rows(self.other.db_path, "reviews")
        self.assertEqual(self.other.import_all(data), {"added": 0, "skipped": 3})
        self.assertEqual(count_rows(self.other.db_path, "problems"), 3)
        self.assertEqual(count_rows(self.other.db_path, "reviews"), n_reviews)

    def test_import_into_same_db_skips_everything(self):
        self.assertEqual(self.store.import_all(self.store.export_all()),
                         {"added": 0, "skipped": 3})
        self.assertEqual(count_rows(self.db_path, "reviews"), 4)

    def test_import_does_not_change_settings(self):
        self.other.import_all(self.store.export_all())
        self.assertEqual(self.other.get_settings(), DEFAULT_SETTINGS)

    def test_drafts_round_trip(self):
        self.store.save_draft(self.a, "print('hi from a')", "python")
        data = json.loads(json.dumps(self.store.export_all()))
        self.assertEqual(len(data["drafts"]), 1)
        self.assertEqual(data["drafts"][0]["code"], "print('hi from a')")
        self.assertNotIn("problem_id", data["drafts"][0])
        self.other.import_all(data)
        uid = self.store.get_problem(self.a)["uid"]
        new_id = [p["id"] for p in self.other.list_problems() if p["uid"] == uid][0]
        self.assertEqual(self.other.get_draft(new_id)["code"], "print('hi from a')")

    def test_import_of_backup_without_drafts_key_still_works(self):
        data = self.store.export_all()
        del data["drafts"]
        result = self.other.import_all(data)
        self.assertEqual(result, {"added": 3, "skipped": 0})
        for p in self.other.list_problems():
            self.assertEqual(self.other.get_draft(p["id"])["code"], "")

    def test_import_skips_a_draft_for_an_unknown_problem_uid(self):
        data = self.store.export_all()
        data["drafts"] = [{"problem_uid": "not-a-real-uid", "code": "x", "language": "python",
                           "updated_at": "2026-01-01T00:00:00+00:00"}]
        result = self.other.import_all(data)
        self.assertEqual(result, {"added": 3, "skipped": 0})
        self.assertEqual(count_rows(self.other.db_path, "drafts"), 0)

    def test_rejects_non_backup_payloads(self):
        for payload in ({}, {"app": "anki"}, {"problems": []}, [], "dsa-review", None, 42,
                        {"app": "DSA-Review", "problems": []}):
            with self.subTest(payload=payload), self.assertRaises(Invalid):
                self.other.import_all(payload)
        self.assertEqual(count_rows(self.other.db_path, "problems"), 0)

    def test_empty_backup(self):
        self.assertEqual(self.other.import_all({"app": "dsa-review"}), {"added": 0, "skipped": 0})
        self.assertEqual(self.other.import_all({"app": "dsa-review", "problems": None,
                                                "reviews": None}), {"added": 0, "skipped": 0})

    def test_problems_without_uid_are_skipped(self):
        data = self.store.export_all()
        data["problems"][1]["uid"] = ""
        del data["problems"][2]["uid"]
        self.assertEqual(self.other.import_all(data), {"added": 1, "skipped": 2})

    def test_import_is_atomic(self):
        data = self.store.export_all()
        data["problems"][2]["url"] = "javascript:alert(1)"
        with self.assertRaises(Invalid):
            self.other.import_all(data)
        self.assertEqual(count_rows(self.other.db_path, "problems"), 0)
        self.assertEqual(count_rows(self.other.db_path, "reviews"), 0)

    def test_import_validates_problem_fields(self):
        data = self.store.export_all()
        data["problems"][0]["tags"] = ["Needs Slugging", "needs-slugging"]
        data["problems"][0]["title"] = "  Padded  "
        self.other.import_all(data)
        p = self.other.list_problems(q="padded")[0]
        self.assertEqual((p["title"], p["tags"]), ("Padded", ["needs-slugging"]))

    # Regression test for a fixed bug (store.import_all): a hand-edited backup with a review dated
    # before 1970 imported fine, but on Windows datetime.astimezone() can't handle such dates, so
    # the Today stats (summary) failed with a 500 from then on. Such entries are now skipped.
    def test_reviews_with_impossible_dates_are_skipped(self):
        data = self.store.export_all()
        data["reviews"][0]["reviewed_at"] = "1900-01-01T00:00:00+00:00"
        data["reviews"][1]["reviewed_at"] = "3500-01-01T00:00:00+00:00"
        self.other.import_all(data)
        self.assertEqual(count_rows(self.other.db_path, "reviews"), 2)
        with connect(self.other.db_path) as c:
            years = {r[0][:4] for r in c.execute("SELECT reviewed_at FROM reviews")}
        self.assertEqual(years, {str(NOW.year)})
        self.other.summary()

    def test_bad_or_orphan_reviews_are_ignored(self):
        data = self.store.export_all()
        data["reviews"][0]["rating"] = 9
        data["reviews"][1]["problem_uid"] = "no-such-uid"
        self.other.import_all(data)
        self.assertEqual(count_rows(self.other.db_path, "reviews"), 2)

    # Regression test for a fixed bug (store.import_all): uids inserted during this import aren't added to `existing`,
    # so a backup containing the same problem twice (e.g. two exports concatenated)
    # crashes with sqlite3.IntegrityError (HTTP 500) instead of skipping the duplicate.
    def test_duplicate_uid_inside_payload_is_skipped(self):
        data = self.store.export_all()
        data["problems"].append(dict(data["problems"][0]))
        self.assertEqual(self.other.import_all(data), {"added": 3, "skipped": 1})

    # Regression test for a fixed bug (store.import_all): malformed entries (missing "card", non-dict problem,
    # non-numeric review rating, naive timestamps...) raise KeyError / AttributeError /
    # ValueError, i.e. HTTP 500, instead of Invalid (HTTP 400).
    def test_malformed_problem_entry_is_invalid(self):
        with self.assertRaises(Invalid):
            self.other.import_all({"app": "dsa-review",
                                   "problems": [{"uid": "u-1", "title": "No card"}]})

    # -------------------------------------------------------------- deck on export/import
    def test_export_includes_deck(self):
        self.store.update_problem(self.b, {"deck": "neetcode"})
        data = self.store.export_all()
        by_id = {p["id"]: p for p in data["problems"]}
        self.assertEqual(by_id[self.a]["deck"], "main")
        self.assertEqual(by_id[self.b]["deck"], "neetcode")

    def test_import_round_trip_preserves_deck(self):
        self.store.update_problem(self.b, {"deck": "neetcode"})
        data = json.loads(json.dumps(self.store.export_all()))
        self.other.import_all(data)
        b_uid = self.store.get_problem(self.b)["uid"]
        imported = next(p for p in self.other.list_problems(scope="all") if p["uid"] == b_uid)
        self.assertEqual(imported["deck"], "neetcode")

    def test_import_missing_deck_defaults_to_main(self):
        data = json.loads(json.dumps(self.store.export_all()))
        for p in data["problems"]:
            del p["deck"]
        self.other.import_all(data)
        self.assertTrue(all(p["deck"] == "main" for p in self.other.list_problems(scope="all")))

    def test_import_invalid_deck_is_rejected(self):
        data = json.loads(json.dumps(self.store.export_all()))
        data["problems"][0]["deck"] = "bogus"
        with self.assertRaises(Invalid):
            self.other.import_all(data)
        # all-or-nothing: nothing from this payload was added
        self.assertEqual(len(self.other.list_problems(scope="all")), 0)

    # ---------------------------------------------------------------- keep vs. reschedule
    FAR_DUE = "2030-01-01T12:00:00+00:00"   # a due date no replay would ever produce

    def tampered_export(self):
        data = json.loads(json.dumps(self.store.export_all()))
        data["problems"][0]["card"]["due"] = self.FAR_DUE
        return data

    def imported_id(self, store, uid):
        return next(p["id"] for p in store.list_problems() if p["uid"] == uid)

    def assert_kept(self, target, data):
        uid = data["problems"][0]["uid"]
        pid = self.imported_id(target, uid)
        self.assertEqual(strip_id(json.loads(problem_row(target.db_path, pid)["card"])),
                         strip_id(data["problems"][0]["card"]))
        self.assertEqual(problem_row(target.db_path, pid)["due"], self.FAR_DUE)
        with connect(target.db_path) as c:
            got = [(strip_id(json.loads(r["card_before"])), strip_id(json.loads(r["card_after"])))
                   for r in c.execute("SELECT * FROM reviews WHERE problem_id = ? ORDER BY id",
                                      (pid,))]
        want = [(strip_id(r["card_before"]), strip_id(r["card_after"]))
                for r in data["reviews"] if r["problem_uid"] == uid]
        self.assertEqual(got, want)

    def assert_recomputed(self, target, data):
        pid = self.imported_id(target, data["problems"][0]["uid"])
        card = get_card(target.db_path, pid)
        self.assertNotEqual(card.due.isoformat(), self.FAR_DUE)
        self.assertTrue(sched.is_anchored(card.last_review) and sched.is_anchored(card.due))
        self.assertEqual(card.last_review, ANCHOR + 3 * DAY)   # latest review: NOW + 3 days
        # recomputed with *this* computer's settings: replaying again changes nothing
        before = snapshot(target.db_path)
        target.reschedule_all(target.scheduler_settings())
        self.assertEqual(snapshot(target.db_path), before)
        # a never-reviewed problem stays new
        new_pid = self.imported_id(target, data["problems"][1]["uid"])
        self.assertEqual(target.get_problem(new_pid)["status"], "new")

    def test_import_keeps_cards_when_scheduling_settings_match(self):
        data = self.tampered_export()
        self.assertEqual(data["settings"]["new_per_day"], 7)   # differs, but doesn't matter
        self.assertEqual(self.other.import_all(data), {"added": 3, "skipped": 0})
        self.assert_kept(self.other, data)

    def test_import_keeps_cards_when_only_neetcode_settings_differ(self):
        data = self.tampered_export()
        data["settings"]["neetcode_in_main"] = True
        data["settings"]["neetcode_new_per_day"] = 99
        self.assertEqual(self.other.import_all(data), {"added": 3, "skipped": 0})
        self.assert_kept(self.other, data)
        # and the neetcode settings themselves aren't pulled in from the backup
        self.assertEqual(self.other.get_settings()["neetcode_in_main"], False)

    def test_import_keeps_cards_when_backup_has_no_settings_and_local_are_defaults(self):
        data = self.tampered_export()
        del data["settings"]
        self.other.import_all(data)
        self.assert_kept(self.other, data)

    def test_match_is_judged_against_local_settings(self):
        data = self.tampered_export()
        data["settings"]["desired_retention"] = 0.85
        self.other.update_settings({"desired_retention": 0.85})
        self.other.import_all(data)
        self.assert_kept(self.other, data)

    def test_import_reschedules_when_scheduling_settings_differ(self):
        from fsrs.scheduler import DEFAULT_PARAMETERS
        params = list(DEFAULT_PARAMETERS)
        params[2] = 10.0
        for key, value in (("desired_retention", 0.8), ("maximum_interval", 30),
                           ("again_next_day", False), ("day_starts_at", 0),
                           ("fsrs_parameters", params)):
            with self.subTest(key=key):
                target = Store(self.tmp / f"import-{key}" / "db.sqlite")
                data = self.tampered_export()
                data["settings"][key] = value
                self.assertEqual(target.import_all(data), {"added": 3, "skipped": 0})
                self.assert_recomputed(target, data)

    def test_import_reschedules_older_schema_versions(self):
        for version in (1, "missing"):
            with self.subTest(version=version):
                target = Store(self.tmp / f"import-v{version}" / "db.sqlite")
                data = self.tampered_export()
                if version == "missing":
                    del data["schema_version"]
                else:
                    data["schema_version"] = version
                target.import_all(data)
                self.assert_recomputed(target, data)

    def test_import_rescheduling_leaves_existing_problems_alone(self):
        local_pid = self.other.create_problem({"title": "Mine"}, 3)["id"]
        tampered = review_state_card(last_review=ANCHOR, stability=2.0, due=utc(2031, 1, 1, 12))
        set_card(self.other.db_path, local_pid, tampered)
        before = problem_row(self.other.db_path, local_pid)
        data = self.tampered_export()
        data["schema_version"] = 1
        self.other.import_all(data)
        self.assertEqual(problem_row(self.other.db_path, local_pid), before)


# ============================================================================ study-day clock
class StudyDayStatusTest(StoreTestCase):
    """status / due_in_days / overdue_days flip at 4am local, not at midnight."""

    def setUp(self):
        super().setUp()
        patcher = frozen_now(local(2026, 1, 15, 12).astimezone(timezone.utc))
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.pid = self.add("Boundary", first_rating=3)["id"]   # reviewed Jan 15 -> due Jan 17

    def at(self, *args):
        self.clock.return_value = local(*args).astimezone(timezone.utc)

    def test_status_flips_at_4am(self):
        cases = (
            ((2026, 1, 16, 12), "scheduled", 1, 0),
            ((2026, 1, 17, 0, 0), "scheduled", 1, 0),
            ((2026, 1, 17, 3, 59, 59), "scheduled", 1, 0),
            ((2026, 1, 17, 4, 0), "due", 0, 0),
            ((2026, 1, 17, 23, 59), "due", 0, 0),
            ((2026, 1, 18, 3, 59), "due", 0, 0),
            ((2026, 1, 18, 4, 0), "due", -1, 1),
            ((2026, 1, 20, 4, 0), "due", -3, 3),
        )
        for when, status, due_in, overdue in cases:
            with self.subTest(when=when):
                self.at(*when)
                p = self.store.get_problem(self.pid)
                self.assertEqual((p["status"], p["due_in_days"], p["overdue_days"]),
                                 (status, due_in, overdue))
                self.assertEqual((p["due_date"], p["due"]),
                                 ("2026-01-17", day_start_utc(date(2026, 1, 17))))
                queued = [q["id"] for q in self.store.queue()["due"]]
                self.assertEqual(self.pid in queued, status == "due")
                s = self.store.summary()
                bucket = 0 if due_in <= 0 else due_in
                self.assertEqual(s["forecast"][bucket]["count"], 1)
                self.assertEqual(s["counts"]["due"], int(status == "due"))

    def test_today_and_retrievability_change_at_4am(self):
        self.at(2026, 1, 17, 3, 59)
        before = (self.store.summary()["today"], self.store.get_problem(self.pid)["retrievability"])
        self.at(2026, 1, 17, 4, 0)
        after = (self.store.summary()["today"], self.store.get_problem(self.pid)["retrievability"])
        self.assertEqual((before[0], after[0]), ("2026-01-16", "2026-01-17"))
        self.assertLess(after[1], before[1])   # one more study day has passed

    def test_midnight_day_start(self):
        self.store.update_settings({"day_starts_at": 0})   # the noon review stays on Jan 15
        self.at(2026, 1, 16, 23, 59)
        p = self.store.get_problem(self.pid)
        self.assertEqual((p["status"], p["due_in_days"]), ("scheduled", 1))
        self.at(2026, 1, 17, 0, 0)
        p = self.store.get_problem(self.pid)
        self.assertEqual((p["status"], p["due_in_days"], p["due"]),
                         ("due", 0, day_start_utc(date(2026, 1, 17), 0)))


class StudyDayReviewTest(StoreTestCase):
    """Reviews through the store use study days, whatever the hour."""

    RATINGS = (1, 3, 3, 3, 3, 3)

    def setUp(self):
        super().setUp()
        patcher = frozen_now(NOW)
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(random.setstate, random.getstate())

    def review_at(self, pid, when, rating, seed=None):
        self.clock.return_value = when.astimezone(timezone.utc)
        if seed is not None:
            random.seed(seed)   # same fuzz draw for the sequences being compared
        return self.store.review_problem(pid, rating)

    def run_sequence(self, title, hour_step):
        """Again, then Good x5, each on its due study date, 1h earlier/later each time."""
        pid = self.add(title)["id"]
        when = local(2026, 1, 15, 20)
        times = []
        for k, rating in enumerate(self.RATINGS):
            p = self.review_at(pid, when, rating, seed=k)
            times.append(when)
            when = at_local(date.fromisoformat(p["due_date"]), 20 + hour_step * (k + 1))
        return pid, times

    def test_23h_and_25h_gaps_give_identical_schedules(self):
        early, early_times = self.run_sequence("Early", -1)
        late, late_times = self.run_sequence("Late", +1)
        self.assertEqual(early_times[1] - early_times[0], timedelta(hours=23))
        self.assertEqual(late_times[1] - late_times[0], timedelta(hours=25))

        he = self.store.get_problem(early)["history"]
        hl = self.store.get_problem(late)["history"]
        for key in ("rating", "interval_days", "next_due", "stability"):
            self.assertEqual([h[key] for h in he], [h[key] for h in hl], key)
        self.assertNotEqual([h["reviewed_at"] for h in he], [h["reviewed_at"] for h in hl])
        self.assertEqual(strip_id(json.loads(problem_row(self.db_path, early)["card"])),
                         strip_id(json.loads(problem_row(self.db_path, late)["card"])))

        intervals = [h["interval_days"] for h in reversed(he)]
        self.assertEqual(intervals[:2], [1, 2])   # the 23h-later Good is a real next-day review
        for shorter, longer in zip(intervals[1:], intervals[2:]):
            self.assertGreater(longer, shorter)
        self.assertGreaterEqual(intervals[-1], 20)
        self.assertEqual([h["kind"] for h in he], ["review"] * 6)

    def test_late_night_reviews_count_for_the_same_study_day(self):
        pid = self.add("Owl")["id"]
        eleven_pm = local(2026, 1, 15, 23)
        two_am = local(2026, 1, 16, 2)
        self.review_at(pid, eleven_pm, 3)
        p = self.review_at(pid, two_am, 3)
        self.assertEqual(get_card(self.db_path, pid).last_review, ANCHOR)
        self.assertEqual([h["reviewed_at"] for h in p["history"]],
                         [two_am.astimezone(timezone.utc).isoformat(),
                          eleven_pm.astimezone(timezone.utc).isoformat()])
        self.assertEqual(p["last_review"], two_am.astimezone(timezone.utc).isoformat())
        self.assertEqual(p["retrievability"], 1.0)
        s = self.store.summary()
        self.assertEqual((s["today"], s["counts"]["reviewed_today"], s["streak_days"]),
                         ("2026-01-15", 2, 1))
        self.assertEqual(self.store.queue()["new_introduced_today"], 1)
        # at 4am the next study day begins
        self.clock.return_value = local(2026, 1, 16, 4).astimezone(timezone.utc)
        s = self.store.summary()
        self.assertEqual((s["today"], s["counts"]["reviewed_today"], s["streak_days"]),
                         ("2026-01-16", 0, 1))
        self.assertEqual(self.store.queue()["new_introduced_today"], 0)

    def test_early_review_on_the_same_day_keeps_the_day_anchor(self):
        pid = self.add("Twice")["id"]
        self.review_at(pid, local(2026, 1, 15, 8), 4)
        first = get_card(self.db_path, pid)
        self.review_at(pid, local(2026, 1, 15, 21), 3)
        second = get_card(self.db_path, pid)
        self.assertEqual((first.last_review, second.last_review), (ANCHOR, ANCHOR))
        self.assertEqual((second.due - second.last_review) % DAY, timedelta(0))


class DaylightSavingStoreTest(StoreTestCase):
    """America/New_York: clocks go back at 2am on Sun 2026-11-01."""

    def setUp(self):
        super().setUp()
        use_timezone(self, "America/New_York")
        patcher = frozen_now(utc(2026, 10, 31, 0))   # Fri Oct 30, 20:00 EDT
        self.clock = patcher.start()
        self.addCleanup(patcher.stop)
        self.pid = self.add("DST", first_rating=3)["id"]

    def test_due_across_fall_back(self):
        card = get_card(self.db_path, self.pid)
        self.assertEqual((card.last_review, card.due), (utc(2026, 10, 30, 12), utc(2026, 11, 1, 12)))
        p = self.store.get_problem(self.pid)
        self.assertEqual((p["due_date"], p["due_in_days"], p["due"]),
                         ("2026-11-01", 2, "2026-11-01T09:00:00+00:00"))   # 04:00 EST
        cases = ((utc(2026, 11, 1, 5, 30), "scheduled", 1),   # 01:30 EDT
                 (utc(2026, 11, 1, 6, 30), "scheduled", 1),   # 01:30 EST (the repeated hour)
                 (utc(2026, 11, 1, 8, 59), "scheduled", 1),   # 03:59 EST
                 (utc(2026, 11, 1, 9, 0), "due", 0),          # 04:00 EST
                 (utc(2026, 11, 2, 8, 59), "due", 0),         # Mon 03:59 EST
                 (utc(2026, 11, 2, 9, 0), "due", -1))         # Mon 04:00 EST
        for now, status, due_in in cases:
            with self.subTest(now=now):
                self.clock.return_value = now
                p = self.store.get_problem(self.pid)
                self.assertEqual((p["status"], p["due_in_days"]), (status, due_in))

    def test_day_bounds_and_forecast_across_the_change(self):
        self.assertEqual(self.store.day_bounds(utc(2026, 10, 31, 16)),
                         (utc(2026, 10, 31, 8), utc(2026, 11, 1, 9)))     # a 25-hour study day
        self.assertEqual(self.store.day_bounds(utc(2026, 11, 1, 16)),
                         (utc(2026, 11, 1, 9), utc(2026, 11, 2, 9)))
        self.assertEqual(self.store.day_bounds(utc(2026, 3, 7, 17)),
                         (utc(2026, 3, 7, 9), utc(2026, 3, 8, 8)))        # a 23-hour study day
        self.clock.return_value = utc(2026, 10, 31, 16)
        s = self.store.summary()
        self.assertEqual([f["date"] for f in s["forecast"][:3]],
                         ["2026-10-31", "2026-11-01", "2026-11-02"])
        self.assertEqual([f["count"] for f in s["forecast"][:3]], [0, 1, 0])
        self.assertEqual((s["today"], s["day_start"], s["day_end"]),
                         ("2026-10-31", utc(2026, 10, 31, 8).isoformat(),
                          utc(2026, 11, 1, 9).isoformat()))

    def test_reviews_across_the_change_use_whole_days(self):
        self.clock.return_value = utc(2026, 11, 1, 1)    # Sat Oct 31, 21:00 EDT
        self.store.review_problem(self.pid, 3)
        self.clock.return_value = utc(2026, 11, 2, 3)    # Sun Nov 1, 22:00 EST
        p = self.store.review_problem(self.pid, 3)
        card = get_card(self.db_path, self.pid)
        self.assertEqual(card.last_review, utc(2026, 11, 1, 12))
        self.assertEqual((card.due - card.last_review) % DAY, timedelta(0))
        h = p["history"][0]
        self.assertEqual(h["interval_days"], (card.due - card.last_review).days)
        due_d = sched.anchor_date(card.due)
        self.assertEqual(h["next_due"], at_local(due_d, 4).isoformat())
        self.assertEqual(datetime.fromisoformat(p["due"]).hour, 9)       # 04:00 EST
        self.assertEqual([x["interval_days"] > 0 for x in p["history"]], [True] * 3)
        s = self.store.summary()
        self.assertEqual((s["today"], s["streak_days"], s["counts"]["reviewed_today"]),
                         ("2026-11-01", 3, 1))


# ============================================================================ migration
class MigrationTest(StoreTestCase):
    """Schema v1 stored real timestamps in the FSRS cards; opening it replays everything."""

    TIMES = ((2026, 1, 3, 21, 17), (2026, 1, 5, 2, 40), (2026, 1, 9, 19, 5))
    RATINGS = (3, 1, 3)

    def make_v1_db(self):
        pid = self.add("Legacy")["id"]
        other = self.add("Never reviewed")["id"]
        times = [local(*t).astimezone(timezone.utc) for t in self.TIMES]
        for t, rating in zip(times, self.RATINGS):
            insert_review(self.db_path, pid, t, rating, anchored=False)
        set_card(self.db_path, pid, review_state_card(
            last_review=times[-1], stability=3.7, due=times[-1] + timedelta(days=3, hours=5)))
        with connect(self.db_path) as c:
            c.execute("PRAGMA user_version = 1")
        self.assertFalse(sched.is_anchored(get_card(self.db_path, pid).due))
        return pid, other, times

    def user_version(self):
        with connect(self.db_path) as c:
            return c.execute("PRAGMA user_version").fetchone()[0]

    def review_rows(self, pid):
        with connect(self.db_path) as c:
            return c.execute("SELECT * FROM reviews WHERE problem_id = ? ORDER BY reviewed_at, id",
                             (pid,)).fetchall()

    def test_v1_database_is_replayed_on_open(self):
        pid, other, times = self.make_v1_db()
        untouched = problem_row(self.db_path, other)
        store = Store(self.db_path)
        self.assertEqual(self.user_version(), SCHEMA_VERSION)

        card = get_card(self.db_path, pid)
        self.assertEqual(card.last_review, utc(2026, 1, 9, 12))
        self.assertTrue(sched.is_anchored(card.due))
        self.assertEqual(problem_row(self.db_path, pid)["due"], card.due.isoformat())
        rows = self.review_rows(pid)
        # 21:17 on the 3rd, 02:40 on the 5th (= study date 4th), 19:05 on the 9th
        self.assertEqual([sched.card_from_json(r["card_after"]).last_review for r in rows],
                         [utc(2026, 1, 3, 12), utc(2026, 1, 4, 12), utc(2026, 1, 9, 12)])
        self.assertEqual(sched.card_from_json(rows[1]["card_after"]).due, utc(2026, 1, 5, 12))
        self.assertIsNone(json.loads(rows[0]["card_before"])["last_review"])
        self.assertEqual(json.loads(rows[2]["card_before"]), json.loads(rows[1]["card_after"]))
        self.assertEqual([r["reviewed_at"] for r in rows], [t.isoformat() for t in times])

        logs = [sched.ReviewLog(card_id=pid, rating=sched.Rating(r), review_datetime=t,
                                review_duration=None) for t, r in zip(times, self.RATINGS)]
        expected = sched.reschedule(pid, logs, store.scheduler_settings())
        self.assertEqual((card.stability, card.difficulty), (expected.stability, expected.difficulty))

        self.assertEqual(problem_row(self.db_path, other), untouched)
        p = store.get_problem(pid)
        self.assertEqual((p["reps"], p["lapses"], p["last_review"]), (3, 1, times[-1].isoformat()))
        self.assertEqual(p["due_date"], card.due.date().isoformat())

    def test_migration_uses_the_saved_day_start(self):
        pid, _, _ = self.make_v1_db()
        with connect(self.db_path) as c:
            c.execute("INSERT INTO settings(key, value) VALUES ('day_starts_at', '0')")
        Store(self.db_path)
        rows = self.review_rows(pid)
        self.assertEqual(sched.card_from_json(rows[1]["card_after"]).last_review,
                         utc(2026, 1, 5, 12))   # 02:40 is already the 5th

    def test_migration_is_deterministic(self):
        self.make_v1_db()
        copy = self.tmp / "copy" / "dsa_review.db"
        copy.parent.mkdir()
        shutil.copyfile(self.db_path, copy)
        Store(self.db_path)
        Store(copy)
        self.assertEqual(snapshot(self.db_path), snapshot(copy))

    def test_v1_database_without_reviews_only_bumps_the_version(self):
        pid = self.add("New")["id"]
        before = problem_row(self.db_path, pid)
        with connect(self.db_path) as c:
            c.execute("PRAGMA user_version = 1")
        Store(self.db_path)
        self.assertEqual(self.user_version(), SCHEMA_VERSION)
        self.assertEqual(problem_row(self.db_path, pid), before)

    def test_v2_database_is_not_replayed_again(self):
        pid, _, _ = self.make_v1_db()
        Store(self.db_path)
        set_card(self.db_path, pid, review_state_card(
            last_review=utc(2026, 1, 9, 12), stability=3.0, due=utc(2026, 3, 1, 12)))
        before = snapshot(self.db_path)
        Store(self.db_path)
        self.assertEqual(snapshot(self.db_path), before)
        self.assertEqual(self.user_version(), SCHEMA_VERSION)


class DeckColumnMigrationTest(StoreTestCase):
    """A database from before the NeetCode deck existed (no `deck` column)."""

    OLD_SCHEMA = """
    CREATE TABLE problems (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        uid         TEXT NOT NULL UNIQUE,
        title       TEXT NOT NULL,
        url         TEXT NOT NULL DEFAULT '',
        source      TEXT NOT NULL DEFAULT '',
        difficulty  TEXT NOT NULL DEFAULT '',
        tags        TEXT NOT NULL DEFAULT '[]',
        prompt      TEXT NOT NULL DEFAULT '',
        insight     TEXT NOT NULL DEFAULT '',
        notes       TEXT NOT NULL DEFAULT '',
        solution    TEXT NOT NULL DEFAULT '',
        language    TEXT NOT NULL DEFAULT 'python',
        suspended   INTEGER NOT NULL DEFAULT 0,
        card        TEXT NOT NULL,
        due         TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE reviews (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        problem_id   INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
        rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
        reviewed_at  TEXT NOT NULL,
        duration_ms  INTEGER,
        card_before  TEXT NOT NULL,
        card_after   TEXT NOT NULL,
        kind         TEXT NOT NULL DEFAULT 'review'
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def make_old_db(self, n_problems=2):
        self.db_path = self.tmp / "legacy" / "dsa_review.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as c:
            c.executescript(self.OLD_SCHEMA)
            now = utc(2026, 1, 1, 12).isoformat()
            for i in range(n_problems):
                card = sched.new_card()
                card.card_id = i + 1
                c.execute(
                    "INSERT INTO problems (uid, title, card, due, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), f"Problem {i + 1}", sched.card_to_json(card), now, now, now))
                c.execute(
                    "INSERT INTO reviews (problem_id, rating, reviewed_at, card_before, card_after, kind) "
                    "VALUES (?, 3, ?, ?, ?, 'review')",
                    (i + 1, now, sched.card_to_json(card), sched.card_to_json(card)))
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")

    def dump_problems(self):
        with connect(self.db_path) as c:
            return [dict(r) for r in c.execute("SELECT * FROM problems ORDER BY id")]

    def test_deck_column_is_added_and_existing_rows_stay_main(self):
        self.make_old_db()
        Store(self.db_path)
        with connect(self.db_path) as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(problems)")}
            self.assertIn("deck", cols)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 2)
            tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("drafts", tables)
        problems = self.dump_problems()
        self.assertEqual(len(problems), 2)
        self.assertTrue(all(p["deck"] == "main" for p in problems))

    def test_old_schema_db_can_use_drafts_after_opening(self):
        """A database from before drafts existed (and before `deck` existed) still works."""
        self.make_old_db(n_problems=1)
        store = Store(self.db_path)
        pid = self.dump_problems()[0]["id"]
        self.assertEqual(store.get_draft(pid), {"code": "", "language": "python", "updated_at": None})
        store.save_draft(pid, "print('ok')")
        self.assertEqual(store.get_draft(pid)["code"], "print('ok')")

    def test_reopening_is_a_no_op(self):
        self.make_old_db()
        Store(self.db_path)
        before = self.dump_problems()
        Store(self.db_path)
        self.assertEqual(self.dump_problems(), before)


class ReviewsDeckColumnMigrationTest(StoreTestCase):
    """A database from just before reviews.deck existed (SCHEMA_VERSION 4: problems.deck
    and the drafts table are already there, only reviews.deck is missing)."""

    def make_v4_db(self):
        self.db_path = self.tmp / "legacy" / "dsa_review.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.db_path) as c:
            # A fresh Store() always creates the current (v5) schema, so build a v4 one
            # by hand instead: same as SCHEMA in store.py, minus reviews.deck.
            c.executescript("""
                CREATE TABLE problems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                    difficulty TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
                    prompt TEXT NOT NULL DEFAULT '', insight TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '', solution TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'python', suspended INTEGER NOT NULL DEFAULT 0,
                    deck TEXT NOT NULL DEFAULT 'main', card TEXT NOT NULL, due TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
                    reviewed_at TEXT NOT NULL, duration_ms INTEGER,
                    card_before TEXT NOT NULL, card_after TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'review'
                );
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE drafts (
                    problem_id INTEGER PRIMARY KEY REFERENCES problems(id) ON DELETE CASCADE,
                    code TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'python', updated_at TEXT NOT NULL
                );
            """)
            now = utc(2026, 1, 1, 12).isoformat()
            card = sched.new_card()
            card.card_id = 1
            c.execute(
                "INSERT INTO problems (uid, title, deck, card, due, created_at, updated_at) "
                "VALUES (?, 'Two Sum', 'main', ?, ?, ?, ?)",
                (str(uuid.uuid4()), sched.card_to_json(card), now, now, now))
            c.execute(
                "INSERT INTO reviews (problem_id, rating, reviewed_at, card_before, card_after, kind) "
                "VALUES (1, 3, ?, ?, ?, 'review')",
                (now, sched.card_to_json(card), sched.card_to_json(card)))
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")

    def test_reviews_deck_column_is_added_as_nullable_and_the_db_still_works(self):
        self.make_v4_db()
        store = Store(self.db_path)
        with connect(self.db_path) as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(reviews)")}
            self.assertIn("deck", cols)
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            # not backfilled - the pre-existing review has no deck of its own
            row = c.execute("SELECT deck FROM reviews WHERE id = 1").fetchone()
            self.assertIsNone(row["deck"])
        # the store still works normally: queue() falls back to the problem's current
        # deck (COALESCE) for that old row, same as before the migration
        q = store.queue()
        self.assertEqual(q["new_introduced_today"], 0)  # reviewed_at is from 2026-01-01, not "today"
        # and a fresh review on the same problem gets a deck of its own
        store.review_problem(1, 3)
        with connect(self.db_path) as c:
            newest = c.execute("SELECT deck FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(newest["deck"], "main")


# ============================================================================ backup
class BackupTest(StoreTestCase):
    NAME = re.compile(r"^dsa_review-\d{8}-\d{6}\.db$")

    def setUp(self):
        super().setUp()
        self.add("Backed up", first_rating=3)
        self.backup_dir = self.tmp / "backups"

    def files(self):
        return sorted(p.name for p in self.backup_dir.glob("dsa_review-*.db"))

    def test_creates_valid_copy(self):
        target = self.store.backup(self.backup_dir)
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, self.backup_dir)
        self.assertRegex(target.name, self.NAME)
        with connect(target) as c:
            self.assertEqual(c.execute("SELECT title FROM problems").fetchone()[0], "Backed up")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 1)
        # the copy is a working database for the app too
        self.assertEqual(Store(target).list_problems()[0]["title"], "Backed up")

    def test_keeps_only_newest(self):
        self.backup_dir.mkdir()
        old = [f"dsa_review-2020010{i}-000000.db" for i in range(1, 6)]
        for name in old:
            (self.backup_dir / name).write_bytes(b"old")
        (self.backup_dir / "unrelated.db").write_bytes(b"keep me")
        target = self.store.backup(self.backup_dir, keep=3)
        self.assertEqual(self.files(), old[3:] + [target.name])
        self.assertTrue((self.backup_dir / "unrelated.db").exists())

    def test_default_keeps_ten(self):
        self.backup_dir.mkdir()
        for i in range(15):
            (self.backup_dir / f"dsa_review-2020-{i:02d}.db").write_bytes(b"")
        target = self.store.backup(self.backup_dir)
        files = self.files()
        self.assertEqual(len(files), 10)
        self.assertEqual(files[-1], target.name)
        self.assertNotIn("dsa_review-2020-00.db", files)

    # Regression test for a fixed bug (store.backup): on Windows, deleting an old backup that
    # antivirus / OneDrive has open raises PermissionError, which crashed the app at startup.
    def test_locked_old_backup_does_not_stop_startup(self):
        self.backup_dir.mkdir()
        for i in range(12):
            (self.backup_dir / f"dsa_review-2020-{i:02d}.db").write_bytes(b"")
        real_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path.name == "dsa_review-2020-00.db":
                raise PermissionError(32, "The process cannot access the file")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", unlink):
            target = self.store.backup(self.backup_dir)
        self.assertTrue(target.is_file())
        files = self.files()
        self.assertIn("dsa_review-2020-00.db", files)      # the locked one is left for next time
        self.assertNotIn("dsa_review-2020-01.db", files)   # the rest were still pruned
        self.assertEqual(files[-1], target.name)

    def test_missing_database_returns_none(self):
        self.db_path.unlink()
        self.assertIsNone(self.store.backup(self.backup_dir))
        self.assertFalse(self.backup_dir.exists())


if __name__ == "__main__":
    unittest.main()


class ClearAllTest(StoreTestCase):
    """Settings → Start over: wipe problems/reviews/attempts, with a backup first."""

    def setUp(self):
        super().setUp()
        self.backups = self.db_path.parent / "backups"
        p1 = self.add("Two Sum", first_rating=3)
        self.add("Valid Anagram")
        self.store.save_draft(p1["id"], "print(1)", "python")
        self.store.update_settings({"new_per_day": 7})

    def test_deletes_everything_and_reports_counts(self):
        result = self.store.clear_all(self.backups)
        self.assertEqual(result["deleted"], {"problems": 2, "reviews": 1, "drafts": 1})
        self.assertEqual(self.store.list_problems(scope="all"), [])
        with self.store.conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM drafts").fetchone()[0], 0)
        self.assertEqual(self.store.summary()["counts"]["total"], 0)

    def test_ids_restart_at_one(self):
        self.store.clear_all(self.backups)
        self.assertEqual(self.add("Fresh start")["id"], 1)

    def test_keeps_settings_by_default(self):
        result = self.store.clear_all(self.backups)
        self.assertFalse(result["settings_reset"])
        self.assertEqual(self.store.get_settings()["new_per_day"], 7)

    def test_can_reset_settings_too(self):
        result = self.store.clear_all(self.backups, reset_settings=True)
        self.assertTrue(result["settings_reset"])
        self.assertEqual(self.store.get_settings(), dict(DEFAULT_SETTINGS))

    def test_backup_is_saved_first_and_restorable(self):
        result = self.store.clear_all(self.backups)
        backup = self.backups / result["backup"]
        self.assertTrue(result["backup"].startswith("before-reset-"))
        self.assertTrue(backup.is_file())
        old = Store(backup)
        self.assertEqual(len(old.list_problems(scope="all")), 2)

    def test_startup_backups_never_prune_reset_backups(self):
        result = self.store.clear_all(self.backups)
        for _ in range(3):
            self.store.backup(self.backups, keep=1)
        self.assertTrue((self.backups / result["backup"]).is_file())
