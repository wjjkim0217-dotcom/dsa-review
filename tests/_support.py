"""Shared helpers for the DSA Review test suite (not a test module itself).

Importing this module puts ``app/`` on ``sys.path`` (app/ is not a package), so
test modules can simply ``import scheduling``, ``import store`` and ``import server``.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
APP_DIR = TESTS_DIR.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import scheduling  # noqa: E402
from fsrs import Card, State  # noqa: E402
from store import Store  # noqa: E402

DAY = timedelta(days=1)


def utc(*args) -> datetime:
    """datetime(...) in UTC, e.g. utc(2026, 1, 15, 12)."""
    return datetime(*args, tzinfo=timezone.utc)


def local(*args) -> datetime:
    """A wall-clock time in the machine's local timezone, as an aware datetime.

    Uses the OS rules for that date (DST-correct) and works on Windows too.
    """
    return datetime(*args).astimezone()


def anchor(when: datetime, day_starts_at: int = 4) -> datetime:
    """The day anchor (noon UTC of the study date) FSRS sees for a review at ``when``."""
    return scheduling.day_anchor(scheduling.study_date(when, day_starts_at))


def review_state_card(*, last_review: datetime, stability: float, difficulty: float = 5.0,
                      due: datetime | None = None, card_id: int = 1) -> Card:
    """A card that has already been reviewed (FSRS Review state).

    Pass day anchors (see ``anchor``/``scheduling.day_anchor``) for realistic cards.
    """
    if due is None:
        due = last_review + timedelta(days=max(1, round(stability)))
    return Card(card_id=card_id, state=State.Review, step=None, stability=stability,
                difficulty=difficulty, due=due, last_review=last_review)


def use_timezone(test: unittest.TestCase, name: str) -> None:
    """Switch the process timezone for one test (restored afterwards).

    Skips the test where time.tzset() is unavailable (Windows) or the zone is unknown.
    """
    if not hasattr(time, "tzset"):
        raise unittest.SkipTest("time.tzset() is not available on this platform")
    old = os.environ.get("TZ")

    def restore():
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()

    os.environ["TZ"] = name
    time.tzset()
    test.addCleanup(restore)
    # An unknown zone silently behaves like UTC; make sure the rules really loaded.
    probe = datetime(2026, 7, 1, 12).astimezone().utcoffset()
    if name != "UTC" and probe == timedelta(0) and \
            datetime(2026, 1, 1, 12).astimezone().utcoffset() == timedelta(0):
        raise unittest.SkipTest(f"timezone {name!r} is not available on this system")


def frozen_now(dt: datetime):
    """Freeze the app's clock (scheduling.utcnow) at ``dt``."""
    return mock.patch.object(scheduling, "utcnow", return_value=dt)


@contextlib.contextmanager
def connect(db_path):
    """Raw SQLite connection that is always committed and closed."""
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def problem_row(db_path, pid) -> dict:
    with connect(db_path) as c:
        row = c.execute("SELECT * FROM problems WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def get_card(db_path, pid) -> Card:
    return scheduling.card_from_json(problem_row(db_path, pid)["card"])


def set_card(db_path, pid, card: Card) -> None:
    """Overwrite a problem's scheduling state (card JSON and the due column)."""
    card.card_id = pid
    with connect(db_path) as c:
        c.execute("UPDATE problems SET card = ?, due = ? WHERE id = ?",
                  (card.to_json(), card.due.astimezone(timezone.utc).isoformat(), pid))


def insert_review(db_path, pid, reviewed_at: datetime, rating: int = 3, *, kind: str = "review",
                  first: bool = False, duration_ms=None, anchored: bool = True,
                  day_starts_at: int = 4) -> int:
    """Insert a review row with a controlled (real) timestamp.

    ``first=True`` stores a never-reviewed card as ``card_before`` (as when a problem
    is pulled out of the new queue); otherwise card_before looks already reviewed.
    The card snapshots use day anchors like the app does, unless ``anchored=False``
    (what a schema-v1 database contained).
    """
    reviewed_at = reviewed_at.astimezone(timezone.utc)
    fsrs_time = anchor(reviewed_at, day_starts_at) if anchored else reviewed_at
    if first:
        before = Card(card_id=pid, due=reviewed_at)
    else:
        before = review_state_card(last_review=fsrs_time - 3 * DAY, stability=3.0,
                                   due=fsrs_time, card_id=pid)
    after = review_state_card(last_review=fsrs_time, stability=4.0, card_id=pid)
    with connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO reviews (problem_id, rating, reviewed_at, duration_ms, card_before, "
            "card_after, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, rating, reviewed_at.isoformat(), duration_ms, before.to_json(),
             after.to_json(), kind),
        )
        return cur.lastrowid


def count_rows(db_path, table: str, where: str = "1=1", params=()) -> int:
    with connect(db_path) as c:
        return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


class TempDirTestCase(unittest.TestCase):
    """Each test gets a fresh temporary directory (``self.tmp``) that is removed afterwards."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="dsa-review-test-")
        self.addCleanup(tmp.cleanup)
        # resolve(): macOS /var -> /private/var, Windows 8.3 short names, etc.
        self.tmp = Path(tmp.name).resolve()


class StoreTestCase(TempDirTestCase):
    """Each test gets a Store on a brand-new database inside a temp directory."""

    def setUp(self):
        super().setUp()
        self.db_path = self.tmp / "data" / "dsa_review.db"
        self.store = Store(self.db_path)

    # small conveniences -------------------------------------------------------
    def add(self, title="Two Sum", first_rating=None, **fields):
        return self.store.create_problem(dict(title=title, **fields), first_rating)
