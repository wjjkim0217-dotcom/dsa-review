"""SQLite storage for problems, review history, and settings.

Everything lives in one file (data/dsa_review.db by default). SQLite ships with
Python, so there's nothing extra to install.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import scheduling as sched
from scheduling import Card, ReviewLog, Rating, SchedulerSettings

SCHEMA_VERSION = 2   # 2 = cards use the study-day clock (see scheduling.py)
DIFFICULTIES = ("", "Easy", "Medium", "Hard")
TEXT_LIMITS = {
    "title": 200, "url": 2000, "source": 100, "prompt": 20000,
    "insight": 1000, "notes": 50000, "solution": 100000, "language": 40,
}
DEFAULT_SETTINGS = {
    "desired_retention": 0.90,   # FSRS target: chance you still remember when it comes back
    "maximum_interval": 60,      # days
    "again_next_day": True,      # "Again" always comes back tomorrow
    "new_per_day": 3,            # unsolved problems introduced per day
    "day_starts_at": 4,          # local hour when a new "day" begins (like Anki)
    "fsrs_parameters": None,     # None = FSRS-6 defaults; a list of 21 numbers from optimize.py
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
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
CREATE INDEX IF NOT EXISTS idx_problems_due ON problems(due);
CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id   INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 4),
    reviewed_at  TEXT NOT NULL,
    duration_ms  INTEGER,
    card_before  TEXT NOT NULL,
    card_after   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'review'  -- 'added' = rating given when the problem was added
);
CREATE INDEX IF NOT EXISTS idx_reviews_problem ON reviews(problem_id, reviewed_at);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def _iso(dt: datetime) -> str:
    return sched.to_utc(dt).isoformat()


def _slug_tag(tag) -> str:
    if not isinstance(tag, str):
        raise Invalid("tags must be text")
    # \w keeps letters from any language (e.g. Korean tags) as well as digits.
    tag = re.sub(r"(?:[^\w+#]|_)+", "-", tag.strip().lower()).strip("-")
    return tag[:40].rstrip("-")


def _as_int(value, name: str, lo: int, hi: int) -> int:
    """Strict integer parsing for JSON input: rejects bools, fractions, inf and nan."""
    if isinstance(value, bool):
        raise Invalid(f"{name} must be a whole number")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise Invalid(f"{name} must be a whole number")
        value = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\s*-?\d{1,12}\s*", value):
        value = int(value)
    if not isinstance(value, int):
        raise Invalid(f"{name} must be a whole number")
    if not lo <= value <= hi:
        raise Invalid(f"{name} must be between {lo} and {hi}")
    return value


def _as_float(value, name: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise Invalid(f"{name} must be a number")
    try:
        value = float(value)
    except ValueError:
        raise Invalid(f"{name} must be a number")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise Invalid(f"{name} must be between {lo} and {hi}")
    return value


def _as_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1) and not isinstance(value, float):
        return bool(value)
    raise Invalid(f"{name} must be true or false")


def parse_rating(value, name: str = "rating") -> int:
    try:
        return _as_int(value, name, 1, 4)
    except Invalid:
        raise Invalid(f"{name} must be 1 (again), 2 (hard), 3 (good) or 4 (easy)")


def parse_duration(value) -> int | None:
    """Milliseconds spent; null/empty means unknown. Clamped to 0..24 hours."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Invalid("duration_ms must be a number of milliseconds")
    return max(0, min(int(value), 24 * 3600 * 1000))


STATUSES = ("", "new", "due", "scheduled", "suspended")


def _naive_local(dt: datetime) -> datetime:
    """UTC -> wall-clock local time (DST-correct for that moment), without tzinfo."""
    return dt.astimezone().replace(tzinfo=None)


def _local_to_utc(naive: datetime) -> datetime:
    """Wall-clock local time -> UTC, using the OS's DST rules for that date."""
    return naive.astimezone().astimezone(timezone.utc)


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version < 2 and c.execute("SELECT 1 FROM reviews LIMIT 1").fetchone():
                # Older databases stored real timestamps in the FSRS cards: replay them.
                self._reschedule_all(c, self.scheduler_settings(self._settings_from(c)))
            if version != SCHEMA_VERSION:
                c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def backup(self, backup_dir: Path, keep: int = 10) -> Path | None:
        """Copy the database into backup_dir (called once at startup)."""
        if not self.db_path.exists():
            return None
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"dsa_review-{stamp}.db"
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(target)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        old = sorted(backup_dir.glob("dsa_review-*.db"))[:-keep]
        for f in old:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                # e.g. Windows: antivirus or OneDrive has the file open. Try again next start;
                # an old backup left behind must never stop the app from starting.
                pass
        return target

    # ------------------------------------------------------------------ settings
    def get_settings(self) -> dict:
        with self.conn() as c:
            return self._settings_from(c)

    @staticmethod
    def _settings_from(c) -> dict:
        out = dict(DEFAULT_SETTINGS)
        for row in c.execute("SELECT key, value FROM settings"):
            if row["key"] in out:
                out[row["key"]] = json.loads(row["value"])
        return out

    def scheduler_settings(self, settings: dict | None = None) -> SchedulerSettings:
        s = settings or self.get_settings()
        return SchedulerSettings(
            desired_retention=float(s["desired_retention"]),
            maximum_interval=int(s["maximum_interval"]),
            again_next_day=bool(s["again_next_day"]),
            parameters=tuple(s["fsrs_parameters"]) if s.get("fsrs_parameters") else None,
            day_starts_at=int(s["day_starts_at"]),
        )

    def update_settings(self, patch: dict) -> dict:
        current = self.get_settings()
        new = dict(current)
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                raise Invalid(f"unknown setting: {key}")
            new[key] = value
        new["desired_retention"] = round(
            _as_float(new["desired_retention"], "desired_retention", 0.70, 0.99), 3)
        new["maximum_interval"] = _as_int(new["maximum_interval"], "maximum_interval", 1, 36500)
        new["new_per_day"] = _as_int(new["new_per_day"], "new_per_day", 0, 100)
        new["day_starts_at"] = _as_int(new["day_starts_at"], "day_starts_at", 0, 23)
        new["again_next_day"] = _as_bool(new["again_next_day"], "again_next_day")
        if new["fsrs_parameters"] is not None:
            try:
                new["fsrs_parameters"] = list(sched.validate_parameters(new["fsrs_parameters"]))
            except ValueError as e:
                raise Invalid(f"fsrs_parameters: {e}")
        sched_keys = ("desired_retention", "maximum_interval", "again_next_day", "fsrs_parameters",
                      "day_starts_at")
        with self.conn() as c:
            for key, value in new.items():
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
            # Same transaction: settings and schedules never get out of step.
            if any(current[k] != new[k] for k in sched_keys):
                self._reschedule_all(c, self.scheduler_settings(new))
        return new

    # ------------------------------------------------------------------ day math
    def day_bounds(self, now: datetime | None = None, settings: dict | None = None):
        """Start and end (UTC) of the current study day in local time."""
        start, end = self._local_day(now, settings)
        return _local_to_utc(start), _local_to_utc(end)

    def _local_day(self, now: datetime | None = None, settings: dict | None = None):
        """Start and end of the current study day as naive local wall-clock times."""
        s = settings or self.get_settings()
        local_now = _naive_local(now or sched.utcnow())
        start = local_now.replace(hour=int(s["day_starts_at"]), minute=0, second=0, microsecond=0)
        if local_now < start:
            start -= timedelta(days=1)
        return start, start + timedelta(days=1)

    # ------------------------------------------------------------------ validation
    def _clean(self, data: dict, partial: bool) -> dict:
        allowed = set(TEXT_LIMITS) | {"difficulty", "tags", "suspended"}
        unknown = set(data) - allowed
        if unknown:
            raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
        out = {}
        for key, limit in TEXT_LIMITS.items():
            if key in data:
                value = data[key]
                if value is None:
                    value = ""
                if not isinstance(value, str):
                    raise Invalid(f"{key} must be text")
                value = value.strip() if key in ("title", "url", "source", "insight", "language") else value
                if len(value) > limit:
                    raise Invalid(f"{key} is too long (max {limit} characters)")
                out[key] = value
        if "title" in out and not out["title"]:
            raise Invalid("title is required")
        if not partial and not out.get("title"):
            raise Invalid("title is required")
        if out.get("url") and not re.match(r"^https?://", out["url"], re.I):
            raise Invalid("url must start with http:// or https://")
        if "difficulty" in data:
            d = data["difficulty"]
            if d is None:
                d = ""
            if not isinstance(d, str):
                raise Invalid("difficulty must be Easy, Medium, Hard or empty")
            d = d.strip().capitalize()
            if d not in DIFFICULTIES:
                raise Invalid("difficulty must be Easy, Medium, Hard or empty")
            out["difficulty"] = d
        if "tags" in data:
            tags = data["tags"]
            if isinstance(tags, str):
                tags = tags.split(",")
            if not isinstance(tags, list):
                raise Invalid("tags must be a list")
            seen = []
            for t in tags:
                slug = _slug_tag(t)
                if slug and slug not in seen:
                    seen.append(slug)
            out["tags"] = json.dumps(seen[:20])
        if "suspended" in data:
            out["suspended"] = 1 if _as_bool(data["suspended"], "suspended") else 0
        if "language" in out and not out["language"]:
            out["language"] = "python"
        return out

    # ------------------------------------------------------------------ serialization
    def _row_to_problem(self, row, stats: dict, settings: SchedulerSettings,
                        today, now: datetime) -> dict:
        """`today` is the current study date (see scheduling.study_date)."""
        card = sched.card_from_json(row["card"])
        new = sched.is_new(card)
        due_d = sched.due_date(card)
        if row["suspended"]:
            status = "suspended"
        elif new:
            status = "new"
        elif due_d <= today:
            status = "due"
        else:
            status = "scheduled"
        st = stats.get(row["id"], {})
        return {
            "id": row["id"],
            "uid": row["uid"],
            "title": row["title"],
            "url": row["url"],
            "source": row["source"],
            "difficulty": row["difficulty"],
            "tags": json.loads(row["tags"]),
            "prompt": row["prompt"],
            "insight": row["insight"],
            "notes": row["notes"],
            "solution": row["solution"],
            "language": row["language"],
            "suspended": bool(row["suspended"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "status": status,
            # `due` is when that study day starts on this computer (e.g. 4am local).
            "due": None if new else sched.study_day_start(due_d, settings.day_starts_at).isoformat(),
            "due_date": None if new else due_d.isoformat(),
            "due_in_days": None if new else (due_d - today).days,
            "overdue_days": None if new else max(0, (today - due_d).days),
            "last_review": st.get("last_reviewed_at"),
            "stability": None if new else round(card.stability, 2),
            "fsrs_difficulty": None if new else round(card.difficulty, 2),
            "retrievability": sched.retrievability(card, settings, now),
            "reps": st.get("reps", 0),
            "lapses": st.get("lapses", 0),
            "last_rating": st.get("last_rating"),
        }

    def _stats_by_problem(self, c) -> dict:
        out = {}
        q = """
            SELECT problem_id,
                   COUNT(*) AS reps,
                   SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS lapses,
                   MAX(reviewed_at) AS last_reviewed_at,
                   (SELECT r2.rating FROM reviews r2 WHERE r2.problem_id = r.problem_id
                     ORDER BY r2.reviewed_at DESC, r2.id DESC LIMIT 1) AS last_rating
            FROM reviews r GROUP BY problem_id
        """
        for row in c.execute(q):
            out[row["problem_id"]] = {
                "reps": row["reps"], "lapses": row["lapses"], "last_rating": row["last_rating"],
                "last_reviewed_at": row["last_reviewed_at"],
            }
        return out

    # ------------------------------------------------------------------ queries
    def list_problems(self, q: str = "", tag: str = "", status: str = "") -> list[dict]:
        settings = self.get_settings()
        ss = self.scheduler_settings(settings)
        now = sched.utcnow()
        today = sched.study_date(now, ss.day_starts_at)
        with self.conn() as c:
            stats = self._stats_by_problem(c)
            rows = c.execute("SELECT * FROM problems ORDER BY due ASC, id ASC").fetchall()
        items = [self._row_to_problem(r, stats, ss, today, now) for r in rows]
        if status not in STATUSES:
            raise Invalid("status must be one of: new, due, scheduled, suspended")
        if q:
            ql = q.lower()
            items = [p for p in items if any(ql in p[k].lower() for k in
                                             ("title", "insight", "source", "prompt", "notes"))
                     or any(ql in t for t in p["tags"])]
        if tag:
            items = [p for p in items if tag in p["tags"]]
        if status:
            items = [p for p in items if p["status"] == status]
        return items

    def get_problem(self, pid: int, with_history: bool = True) -> dict:
        settings = self.get_settings()
        ss = self.scheduler_settings(settings)
        now = sched.utcnow()
        today = sched.study_date(now, ss.day_starts_at)
        with self.conn() as c:
            row = c.execute("SELECT * FROM problems WHERE id = ?", (pid,)).fetchone()
            if not row:
                raise NotFound(f"problem {pid} not found")
            stats = self._stats_by_problem(c)
            history = []
            if with_history:
                for r in c.execute(
                    "SELECT id, rating, reviewed_at, duration_ms, card_after, kind FROM reviews "
                    "WHERE problem_id = ? ORDER BY reviewed_at DESC, id DESC", (pid,)
                ):
                    after = sched.card_from_json(r["card_after"])
                    next_d = sched.due_date(after)
                    history.append({
                        "id": r["id"],
                        "rating": r["rating"],
                        "rating_name": sched.RATING_NAMES[r["rating"]],
                        "kind": r["kind"],
                        "reviewed_at": r["reviewed_at"],
                        "duration_ms": r["duration_ms"],
                        "next_due": sched.study_day_start(next_d, ss.day_starts_at).isoformat() if next_d else None,
                        "interval_days": (after.due - after.last_review).days if next_d else None,
                        "stability": round(after.stability, 2) if after.stability else None,
                    })
        problem = self._row_to_problem(row, stats, ss, today, now)
        problem["history"] = history
        problem["preview"] = sched.preview(sched.card_from_json(row["card"]), ss, now)
        return problem

    def tags(self) -> list[dict]:
        counts: dict[str, int] = {}
        with self.conn() as c:
            for row in c.execute("SELECT tags FROM problems"):
                for t in json.loads(row["tags"]):
                    counts[t] = counts.get(t, 0) + 1
        return [{"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    def queue(self, tag: str = "") -> dict:
        """Problems to work on today: due reviews (weakest memory first), then new ones."""
        settings = self.get_settings()
        now = sched.utcnow()
        day_start, day_end = self.day_bounds(now, settings)
        items = self.list_problems(tag=tag)
        due = [p for p in items if p["status"] == "due"]
        # Lowest predicted recall first; ties broken by most overdue.
        due.sort(key=lambda p: (p["retrievability"] if p["retrievability"] is not None else 0,
                                p["due"] or ""))
        with self.conn() as c:
            # Only count problems that came out of the "new" queue, not ones you added
            # right after solving them. (Parsed in Python: older SQLite builds lack JSON functions.)
            introduced_today = sum(
                1 for (before,) in c.execute(
                    "SELECT card_before FROM reviews WHERE kind = 'review' "
                    "AND reviewed_at >= ? AND reviewed_at < ?",
                    (_iso(day_start), _iso(day_end)),
                )
                if json.loads(before).get("last_review") is None
            )
        new_left = max(0, int(settings["new_per_day"]) - introduced_today)
        new_all = sorted((p for p in items if p["status"] == "new"), key=lambda p: p["id"])
        return {
            "due": due,
            "new": new_all[:new_left],
            "new_waiting": len(new_all),
            "new_left_today": new_left,
            "new_introduced_today": introduced_today,
        }

    def summary(self) -> dict:
        settings = self.get_settings()
        now = sched.utcnow()
        day_start, day_end = self.day_bounds(now, settings)
        local_start, _ = self._local_day(now, settings)
        items = self.list_problems()
        active = [p for p in items if not p["suspended"]]

        # 14-day forecast of reviews by study day (day 0 = today, includes overdue).
        forecast = []
        for i in range(14):
            count = sum(1 for p in active if p["due_in_days"] is not None
                        and (p["due_in_days"] == i or (i == 0 and p["due_in_days"] < 0)))
            forecast.append({"date": (local_start + timedelta(days=i)).date().isoformat(),
                             "count": count})

        with self.conn() as c:
            reviewed_today = c.execute(
                "SELECT COUNT(*) FROM reviews WHERE reviewed_at >= ? AND reviewed_at < ?",
                (_iso(day_start), _iso(day_end)),
            ).fetchone()[0]
            # Review days, bucketed by study day, for the streak.
            days = set()
            offset = timedelta(hours=int(settings["day_starts_at"]))
            for (ts,) in c.execute("SELECT reviewed_at FROM reviews"):
                days.add((_naive_local(datetime.fromisoformat(ts)) - offset).date())
            # Success rate on non-first reviews in the last 30 days.
            since = _iso(now - timedelta(days=30))
            rows = c.execute(
                """SELECT r.rating FROM reviews r
                   WHERE r.reviewed_at >= ?
                     AND EXISTS (SELECT 1 FROM reviews p WHERE p.problem_id = r.problem_id
                                 AND (p.reviewed_at < r.reviewed_at
                                      OR (p.reviewed_at = r.reviewed_at AND p.id < r.id)))""",
                (since,),
            ).fetchall()
        today = local_start.date()
        streak, d = 0, today
        if d not in days:
            d -= timedelta(days=1)   # today not done yet doesn't break the streak
        while d in days:
            streak += 1
            d -= timedelta(days=1)
        passed = sum(1 for r in rows if r["rating"] > 1)
        return {
            "now": now.isoformat(),
            "day_start": day_start.isoformat(),
            "day_end": day_end.isoformat(),
            "today": local_start.date().isoformat(),
            "counts": {
                "total": len(items),
                "active": len(active),
                "suspended": len(items) - len(active),
                "new": sum(1 for p in active if p["status"] == "new"),
                "due": sum(1 for p in active if p["status"] == "due"),
                "scheduled": sum(1 for p in active if p["status"] == "scheduled"),
                "reviewed_today": reviewed_today,
            },
            "streak_days": streak,
            "recall_rate_30d": round(passed / len(rows), 3) if rows else None,
            "reviews_30d": len(rows),
            "forecast": forecast,
            "settings": settings,
        }

    # ------------------------------------------------------------------ mutations
    def create_problem(self, data: dict, first_rating: int | None = None,
                       duration_ms: int | None = None) -> dict:
        data = dict(data)
        fields = self._clean(data, partial=False)
        # Validate everything before writing, so a bad rating never leaves a half-added problem.
        if first_rating in (None, "", 0):   # 0 / null = "haven't solved it yet"
            first_rating = None
        else:
            first_rating = parse_rating(first_rating, "first_rating")
        duration_ms = parse_duration(duration_ms)
        ss = self.scheduler_settings() if first_rating is not None else None
        now = sched.utcnow()
        card = sched.new_card()
        fields.setdefault("language", "python")
        fields["uid"] = str(uuid.uuid4())
        fields["card"] = sched.card_to_json(card)
        fields["due"] = _iso(card.due)
        fields["created_at"] = fields["updated_at"] = _iso(now)
        with self.conn() as c:
            cols = ", ".join(fields)
            qs = ", ".join("?" for _ in fields)
            cur = c.execute(f"INSERT INTO problems ({cols}) VALUES ({qs})", list(fields.values()))
            pid = cur.lastrowid
            card.card_id = pid
            c.execute("UPDATE problems SET card = ? WHERE id = ?", (sched.card_to_json(card), pid))
            if first_rating is not None:
                self._review_in(c, pid, first_rating, duration_ms, "added", ss, now)
        return self.get_problem(pid)

    def update_problem(self, pid: int, data: dict) -> dict:
        fields = self._clean(data, partial=True)
        if not fields:
            return self.get_problem(pid)
        fields["updated_at"] = _iso(sched.utcnow())
        with self.conn() as c:
            sets = ", ".join(f"{k} = ?" for k in fields)
            cur = c.execute(f"UPDATE problems SET {sets} WHERE id = ?", [*fields.values(), pid])
            if cur.rowcount == 0:
                raise NotFound(f"problem {pid} not found")
        return self.get_problem(pid)

    def delete_problem(self, pid: int) -> None:
        with self.conn() as c:
            cur = c.execute("DELETE FROM problems WHERE id = ?", (pid,))
            if cur.rowcount == 0:
                raise NotFound(f"problem {pid} not found")

    def review_problem(self, pid: int, rating, duration_ms=None, kind: str = "review") -> dict:
        rating = parse_rating(rating)
        duration_ms = parse_duration(duration_ms)
        ss = self.scheduler_settings()
        with self.conn() as c:
            self._review_in(c, pid, rating, duration_ms, kind, ss, sched.utcnow())
        return self.get_problem(pid)

    def _review_in(self, c, pid: int, rating: int, duration_ms, kind: str,
                   ss: SchedulerSettings, now: datetime) -> None:
        row = c.execute("SELECT card FROM problems WHERE id = ?", (pid,)).fetchone()
        if not row:
            raise NotFound(f"problem {pid} not found")
        before = sched.card_from_json(row["card"])
        before.card_id = pid
        after, _log = sched.review(before, rating, ss, now, duration_ms)
        c.execute(
            "INSERT INTO reviews (problem_id, rating, reviewed_at, duration_ms, card_before, card_after, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, rating, _iso(now), duration_ms, sched.card_to_json(before),
             sched.card_to_json(after), kind),
        )
        c.execute(
            "UPDATE problems SET card = ?, due = ? WHERE id = ?",
            (sched.card_to_json(after), _iso(after.due), pid),
        )

    def undo_last_review(self, pid: int, review_id=None) -> dict:
        """Remove the problem's latest review and restore the schedule it replaced.

        ``review_id`` (optional) is the review the caller means to undo, e.g. the one
        an "Undo" pop-up was shown for. If that review is no longer the latest one
        (already undone, or rated again since), nothing changes, so a stale Undo
        button can never delete an older review from the history.
        """
        if review_id is not None:
            review_id = _as_int(review_id, "review_id", 1, 2**63 - 1)
        with self.conn() as c:
            if not c.execute("SELECT 1 FROM problems WHERE id = ?", (pid,)).fetchone():
                raise NotFound(f"problem {pid} not found")
            row = c.execute(
                "SELECT id, card_before FROM reviews WHERE problem_id = ? "
                "ORDER BY reviewed_at DESC, id DESC LIMIT 1", (pid,)
            ).fetchone()
            if not row:
                raise Invalid("this problem has no reviews to undo")
            if review_id is not None and row["id"] != review_id:
                raise Invalid("That review was already undone, or the problem was rated again since.")
            card = sched.card_from_json(row["card_before"])
            c.execute("DELETE FROM reviews WHERE id = ?", (row["id"],))
            c.execute("UPDATE problems SET card = ?, due = ? WHERE id = ?",
                      (row["card_before"], _iso(card.due), pid))
        return self.get_problem(pid)

    def reschedule_all(self, ss: SchedulerSettings) -> int:
        with self.conn() as c:
            return self._reschedule_all(c, ss)

    def _reschedule_all(self, c, ss: SchedulerSettings, ids=None) -> int:
        """Replay every problem's history under new settings.

        Also rewrites each review's before/after snapshot, so "Undo" after a
        settings change restores a schedule that matches the new settings.
        Fuzz is seeded per review, so results are repeatable but still spread out.
        """
        count = 0
        if ids is None:
            ids = [r["id"] for r in c.execute("SELECT id FROM problems")]
        for pid in ids:
            rows = c.execute(
                "SELECT id, rating, reviewed_at, duration_ms FROM reviews WHERE problem_id = ? "
                "ORDER BY reviewed_at, id", (pid,)).fetchall()
            if not rows:
                continue
            card = Card(card_id=pid, due=ss.anchor(sched.parse_dt(rows[0]["reviewed_at"])))
            for r in rows:
                before = card
                card = sched.replay_one(before, r["rating"], ss,
                                        sched.parse_dt(r["reviewed_at"]), r["duration_ms"],
                                        seed=f"{pid}:{r['id']}")
                c.execute("UPDATE reviews SET card_before = ?, card_after = ? WHERE id = ?",
                          (sched.card_to_json(before), sched.card_to_json(card), r["id"]))
            c.execute("UPDATE problems SET card = ?, due = ? WHERE id = ?",
                      (sched.card_to_json(card), _iso(card.due), pid))
            count += 1
        return count

    # ------------------------------------------------------------------ backup / restore
    def export_all(self) -> dict:
        with self.conn() as c:
            problems = [dict(r) for r in c.execute("SELECT * FROM problems ORDER BY id")]
            reviews = [dict(r) for r in c.execute("SELECT * FROM reviews ORDER BY id")]
        uid_by_id = {p["id"]: p["uid"] for p in problems}
        for p in problems:
            p["tags"] = json.loads(p["tags"])
            p["card"] = json.loads(p["card"])
            p["suspended"] = bool(p["suspended"])
        for r in reviews:
            r["problem_uid"] = uid_by_id[r.pop("problem_id")]
            r["card_before"] = json.loads(r["card_before"])
            r["card_after"] = json.loads(r["card_after"])
            r.pop("id")
        return {
            "app": "dsa-review",
            "schema_version": SCHEMA_VERSION,
            "exported_at": _iso(sched.utcnow()),
            "settings": self.get_settings(),
            "problems": problems,
            "reviews": reviews,
        }

    def import_all(self, payload: dict) -> dict:
        """Merge a backup: problems whose uid already exists are skipped.

        The whole import is one transaction: a malformed file changes nothing.
        """
        if not isinstance(payload, dict) or payload.get("app") != "dsa-review":
            raise Invalid("this doesn't look like a dsa-review backup file")
        problems = payload.get("problems") or []
        reviews = payload.get("reviews") or []
        if not isinstance(problems, list) or not isinstance(reviews, list):
            raise Invalid("backup file is malformed (problems/reviews must be lists)")
        added = skipped = 0
        with self.conn() as c:
            existing = {r["uid"] for r in c.execute("SELECT uid FROM problems")}
            id_by_uid = {}
            for i, p in enumerate(problems, 1):
                try:
                    uid = p.get("uid")
                    if not isinstance(uid, str) or not uid or uid in existing:
                        skipped += 1
                        continue
                    fields = self._clean({k: p[k] for k in
                                          (*TEXT_LIMITS, "difficulty", "tags", "suspended") if k in p},
                                         partial=False)
                    card = self._card_from_backup(p["card"], 0)
                    now_iso = _iso(sched.utcnow())
                    fields.update(uid=uid, due=_iso(card.due),
                                  created_at=_iso(sched.parse_dt(p["created_at"])) if p.get("created_at") else now_iso,
                                  updated_at=_iso(sched.parse_dt(p["updated_at"])) if p.get("updated_at") else now_iso,
                                  card="{}")
                except Invalid as e:
                    raise Invalid(f"backup problem #{i}: {e}")
                except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as e:
                    raise Invalid(f"backup problem #{i} is malformed ({e})")
                cols = ", ".join(fields)
                qs = ", ".join("?" for _ in fields)
                cur = c.execute(f"INSERT INTO problems ({cols}) VALUES ({qs})", list(fields.values()))
                pid = cur.lastrowid
                card.card_id = pid
                c.execute("UPDATE problems SET card = ? WHERE id = ?", (sched.card_to_json(card), pid))
                id_by_uid[uid] = pid
                existing.add(uid)
                added += 1
            for i, r in enumerate(reviews, 1):
                try:
                    pid = id_by_uid.get(r.get("problem_uid"))
                    if pid is None:
                        continue
                    rating = parse_rating(r["rating"])
                    reviewed_at = sched.parse_dt(r["reviewed_at"])
                    if not 1971 <= reviewed_at.year <= 2999:
                        # Windows can't convert such dates to local time, which the
                        # Today stats need; a real review is never dated like this.
                        raise ValueError("review date out of range")
                    before = self._card_from_backup(r["card_before"], pid)
                    after = self._card_from_backup(r["card_after"], pid)
                    duration = parse_duration(r.get("duration_ms"))
                except (Invalid, KeyError, TypeError, ValueError, AttributeError, OverflowError):
                    continue  # a bad history entry only loses that entry, not the problem
                c.execute(
                    "INSERT INTO reviews (problem_id, rating, reviewed_at, duration_ms, card_before, card_after, kind) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (pid, rating, _iso(reviewed_at), duration, sched.card_to_json(before),
                     sched.card_to_json(after), "added" if r.get("kind") == "added" else "review"),
                )
            # Backups from an older version, or made with different scheduling settings,
            # get their schedules recomputed with this computer's settings.
            local = self._settings_from(c)
            source = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            keys = ("desired_retention", "maximum_interval", "again_next_day",
                    "fsrs_parameters", "day_starts_at")
            same = all(source.get(k, DEFAULT_SETTINGS[k]) == local[k] for k in keys)
            if id_by_uid and (payload.get("schema_version") != SCHEMA_VERSION or not same):
                self._reschedule_all(c, self.scheduler_settings(local), ids=list(id_by_uid.values()))
        return {"added": added, "skipped": skipped}

    @staticmethod
    def _card_from_backup(data, card_id: int) -> Card:
        if not isinstance(data, dict):
            raise ValueError("card must be an object")
        card = Card.from_dict({**data, "card_id": card_id})
        for attr in ("due", "last_review"):
            value = getattr(card, attr)
            if value is not None:
                value = sched.to_utc(value)
                # Windows can't convert dates outside this range to local time.
                if not 1971 <= value.year <= 2999:
                    raise ValueError(f"card {attr} is out of range")
                setattr(card, attr, value)
        if card.due is None:
            raise ValueError("card has no due date")
        return card
