"""Tests for app/neetcode.py (the NeetCode 150 tracker) and GET /api/neetcode."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import StoreTestCase  # noqa: E402  (puts app/ on sys.path)

import neetcode  # noqa: E402


def lib_problem(id=1, title="Two Sum", url="", reps=0, stability=None, suspended=False, **extra):
    """A minimal stand-in for what store.list_problems() returns."""
    status = "suspended" if suspended else ("new" if reps == 0 else "scheduled")
    row = {
        "id": id, "title": title, "url": url, "reps": reps, "lapses": 0,
        "stability": stability, "suspended": suspended, "status": status,
        "due": None, "due_date": None, "due_in_days": None, "retrievability": None,
        "last_review": None, "deck": "neetcode",  # already in the NeetCode deck, unless overridden
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------- slug parsing
class ExtractSlugTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(neetcode.extract_leetcode_slug("https://leetcode.com/problems/two-sum/"), "two-sum")

    def test_no_trailing_slash(self):
        self.assertEqual(neetcode.extract_leetcode_slug("https://leetcode.com/problems/two-sum"), "two-sum")

    def test_uppercase_path(self):
        self.assertEqual(neetcode.extract_leetcode_slug("https://LeetCode.com/problems/Two-Sum/"), "two-sum")

    def test_www(self):
        self.assertEqual(neetcode.extract_leetcode_slug("https://www.leetcode.com/problems/two-sum/"), "two-sum")

    def test_query_and_fragment(self):
        self.assertEqual(
            neetcode.extract_leetcode_slug("https://leetcode.com/problems/two-sum/?envType=list#top"),
            "two-sum")

    def test_description_suffix(self):
        self.assertEqual(
            neetcode.extract_leetcode_slug("https://leetcode.com/problems/two-sum/description/"), "two-sum")

    def test_solutions_suffix(self):
        self.assertEqual(
            neetcode.extract_leetcode_slug("https://leetcode.com/problems/two-sum/solutions/12345/whatever/"),
            "two-sum")

    def test_leetcode_cn(self):
        self.assertEqual(neetcode.extract_leetcode_slug("https://leetcode.cn/problems/two-sum/"), "two-sum")

    def test_http_scheme(self):
        self.assertEqual(neetcode.extract_leetcode_slug("http://leetcode.com/problems/two-sum/"), "two-sum")

    def test_neetcode_io_is_not_a_slug(self):
        self.assertIsNone(neetcode.extract_leetcode_slug("https://neetcode.io/problems/two-sum"))

    def test_unrelated_domain(self):
        self.assertIsNone(neetcode.extract_leetcode_slug("https://example.com/problems/two-sum/"))

    def test_non_problems_path(self):
        self.assertIsNone(neetcode.extract_leetcode_slug("https://leetcode.com/explore/two-sum/"))

    def test_empty_or_none(self):
        self.assertIsNone(neetcode.extract_leetcode_slug(""))
        self.assertIsNone(neetcode.extract_leetcode_slug(None))

    def test_malformed_url_does_not_raise(self):
        self.assertIsNone(neetcode.extract_leetcode_slug("not a url at all :://"))


# ---------------------------------------------------------------- data file integrity
class DataFileTest(unittest.TestCase):
    def setUp(self):
        self.data = neetcode.get_data()

    def test_150_problems(self):
        self.assertEqual(len(self.data["problems"]), 150)

    def test_unique_slugs(self):
        slugs = [p["slug"] for p in self.data["problems"]]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_18_categories(self):
        self.assertEqual(len(self.data["categories"]), 18)
        self.assertEqual(len(set(self.data["categories"])), 18)

    def test_every_problem_category_is_listed(self):
        cats = set(self.data["categories"])
        for p in self.data["problems"]:
            self.assertIn(p["category"], cats)

    def test_ids_are_1_to_150(self):
        ids = sorted(p["id"] for p in self.data["problems"])
        self.assertEqual(ids, list(range(1, 151)))

    def test_cached(self):
        self.assertIs(neetcode.get_data(), self.data)


# ---------------------------------------------------------------- matching & status
class BuildTrackerTest(unittest.TestCase):
    def payload_for(self, id):
        return next(p for p in neetcode.get_data()["problems"] if p["id"] == id)

    def test_no_library_problems_all_not_started(self):
        out = neetcode.build_tracker([])
        self.assertEqual(out["totals"]["total"], 150)
        self.assertEqual(out["totals"]["not_started"], 150)
        self.assertEqual(out["totals"]["solved"], 0)
        self.assertTrue(all(p["status"] == "not_started" and p["problem"] is None for p in out["problems"]))

    def test_slug_match_added_status(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=0)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "added")
        self.assertEqual(p["problem"]["id"], lp["id"])

    def test_title_fallback_match(self):
        lp = lib_problem(title="  two   sum  ", url="", reps=1, stability=5.0)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "solved")

    def test_slug_takes_priority_over_bad_title(self):
        # Title doesn't match anything, but the slug does.
        lp = lib_problem(title="My Own Name For It", url="https://leetcode.com/problems/two-sum/", reps=2)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "solved")

    def test_unmatched_problem_stays_not_started(self):
        lp = lib_problem(title="Some Random Problem Nobody Made", url="", reps=3)
        out = neetcode.build_tracker([lp])
        self.assertTrue(all(p["status"] == "not_started" for p in out["problems"]))

    def test_status_solved(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=1, stability=5.0, suspended=False)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "solved")

    def test_status_mastered(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=3, stability=21.0, suspended=False)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "mastered")

    def test_status_mastered_requires_not_suspended(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=3, stability=40.0, suspended=True)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "solved")

    def test_status_mastered_requires_stability_threshold(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=3, stability=20.9, suspended=False)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "solved")

    def test_duplicate_match_prefers_most_reps(self):
        weak = lib_problem(id=5, url="https://leetcode.com/problems/two-sum/", reps=1, stability=1.0)
        strong = lib_problem(id=9, url="https://leetcode.com/problems/two-sum/", reps=4, stability=25.0)
        out = neetcode.build_tracker([weak, strong])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["problem"]["id"], 9)
        self.assertEqual(p["status"], "mastered")

    def test_duplicate_match_ties_prefer_lowest_id(self):
        a = lib_problem(id=9, url="https://leetcode.com/problems/two-sum/", reps=1)
        b = lib_problem(id=3, url="https://leetcode.com/problems/two-sum/", reps=1)
        out = neetcode.build_tracker([a, b])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["problem"]["id"], 3)

    def test_each_library_problem_matches_at_most_one(self):
        # Same library problem's slug can't somehow appear twice; sanity check totals.
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=1)
        out = neetcode.build_tracker([lp])
        solved = [p for p in out["problems"] if p["status"] != "not_started"]
        self.assertEqual(len(solved), 1)

    def test_totals_and_category_counts(self):
        solved = lib_problem(id=1, url="https://leetcode.com/problems/two-sum/", reps=1, stability=1.0)
        mastered = lib_problem(id=2, url="https://leetcode.com/problems/contains-duplicate/",
                               reps=5, stability=30.0)
        added = lib_problem(id=3, url="https://leetcode.com/problems/valid-anagram/", reps=0)
        out = neetcode.build_tracker([solved, mastered, added])
        t = out["totals"]
        self.assertEqual(t["solved"], 2)  # solved + mastered both count as solved
        self.assertEqual(t["mastered"], 1)
        self.assertEqual(t["added"], 1)
        self.assertEqual(t["not_started"], 147)
        self.assertEqual(t["total"], 150)
        arrays_hashing = next(c for c in out["categories"] if c["name"] == "Arrays & Hashing")
        self.assertEqual(arrays_hashing["solved"], 2)
        self.assertEqual(arrays_hashing["mastered"], 1)
        self.assertEqual(arrays_hashing["added"], 1)
        self.assertGreater(arrays_hashing["total"], 0)
        # category order matches the roadmap order in the data file
        self.assertEqual([c["name"] for c in out["categories"]], neetcode.get_data()["categories"])

    def test_by_difficulty_and_blind75(self):
        two_sum = self.payload_for(next(p["id"] for p in neetcode.get_data()["problems"]
                                        if p["slug"] == "two-sum"))
        lp = lib_problem(url=two_sum["leetcode_url"], reps=1, stability=1.0)
        out = neetcode.build_tracker([lp])
        diff = two_sum["difficulty"]
        self.assertEqual(out["totals"]["by_difficulty"][diff]["solved"], 1)
        if two_sum["blind75"]:
            self.assertEqual(out["totals"]["blind75"]["solved"], 1)

    def test_problem_payload_shape(self):
        lp = lib_problem(url="https://leetcode.com/problems/two-sum/", reps=2, stability=10.0,
                         due="2026-10-01T00:00:00+00:00", due_date="2026-10-01", due_in_days=3,
                         retrievability=0.8, last_review="2026-09-01T00:00:00+00:00", lapses=1)
        out = neetcode.build_tracker([lp])
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        for key in ("id", "title", "slug", "leetcode_number", "category", "difficulty", "blind75",
                    "leetcode_url", "video_url", "solution_url", "status", "problem"):
            self.assertIn(key, p)
        for key in ("id", "status", "due", "due_date", "due_in_days", "retrievability",
                    "stability", "reps", "lapses", "last_review", "suspended"):
            self.assertIn(key, p["problem"])
        self.assertEqual(p["problem"]["reps"], 2)
        self.assertEqual(p["problem"]["lapses"], 1)


# ---------------------------------------------------------------- store integration
class TrackerFromStoreTest(StoreTestCase):
    """Uses the real store.list_problems() shape, not the lib_problem() stand-in."""

    def test_build_tracker_from_real_store_rows(self):
        self.store.create_problem({"title": "Two Sum", "url": "https://leetcode.com/problems/two-sum/",
                                   "deck": "neetcode"})
        out = neetcode.build_tracker(self.store.list_problems(scope="all"))
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "added")

        self.store.create_problem(
            {"title": "Contains Duplicate", "url": "https://leetcode.com/problems/contains-duplicate/",
             "deck": "neetcode"},
            first_rating=3)
        out = neetcode.build_tracker(self.store.list_problems(scope="all"))
        p = next(p for p in out["problems"] if p["slug"] == "contains-duplicate")
        self.assertEqual(p["status"], "solved")

    def test_main_deck_problem_is_not_matched_but_is_adoptable(self):
        self.store.create_problem({"title": "Two Sum", "url": "https://leetcode.com/problems/two-sum/"})
        out = neetcode.build_tracker(self.store.list_problems(scope="all"))
        p = next(p for p in out["problems"] if p["slug"] == "two-sum")
        self.assertEqual(p["status"], "not_started")
        self.assertEqual(len(out["adoptable"]), 1)
        self.assertEqual(out["adoptable"][0]["nc_title"], "Two Sum")


if __name__ == "__main__":
    unittest.main()
