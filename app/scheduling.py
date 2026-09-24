"""Scheduling logic: a thin wrapper around the FSRS-6 algorithm (PyPI package `fsrs`).

Why FSRS-6?
    It's the newest production version of the Free Spaced Repetition Scheduler,
    the algorithm Anki uses. Instead of a fixed exponential forgetting curve
    (Ebbinghaus), it models each problem with two numbers:
      * stability  (S): days until your chance of remembering drops to 90%
      * difficulty (D): 1-10, how hard the problem is for *you*
    and a power-law forgetting curve R(t) = (1 + f * t / S) ** -decay.
    Each review updates S and D, and the next review is scheduled for when
    R is predicted to fall to your "desired retention".

Choices made for coding problems (different from Anki's flashcard defaults):
    * No same-day learning steps. Recoding a problem 10 minutes later is pointless,
      so every interval is at least 1 day.
    * "Again" (couldn't solve it) always brings the problem back tomorrow.
    * A maximum interval (default 60 days) so nothing disappears for a year
      in the middle of interview prep.
    * Time is counted in *study days* (a day starts at 4am local by default),
      like Anki. py-fsrs counts elapsed time in whole 24-hour blocks, so a
      problem due "tomorrow" but practiced 23 hours later would count as a
      same-day repeat and barely move. To avoid that, FSRS only ever sees
      review times snapped to noon UTC of the study date ("day anchors"), so
      elapsed time is always an exact number of study days. The real review
      timestamps are still stored in the reviews table.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fsrs import Card, Rating, ReviewLog, Scheduler, State
from fsrs.scheduler import DEFAULT_PARAMETERS

RATING_NAMES = {1: "again", 2: "hard", 3: "good", 4: "easy"}


def utcnow() -> datetime:
    # fsrs requires datetime.timezone.utc exactly (not zoneinfo UTC, not naive).
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return to_utc(datetime.fromisoformat(value))


# ---------------------------------------------------------------- study-day clock
def study_date(when: datetime, day_starts_at: int = 4) -> date:
    """The local study date an instant belongs to (a day starts at `day_starts_at` o'clock)."""
    local = to_utc(when).astimezone().replace(tzinfo=None)  # OS DST rules for that instant
    return (local - timedelta(hours=day_starts_at)).date()


def day_anchor(d: date) -> datetime:
    """The instant FSRS sees for every review made on study date `d`."""
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)


def anchor_date(dt: datetime) -> date:
    """Inverse of day_anchor (tolerant of legacy, non-anchored timestamps)."""
    return to_utc(dt).date()


def study_day_start(d: date, day_starts_at: int = 4) -> datetime:
    """Real UTC instant when study date `d` begins on this computer."""
    naive = datetime(d.year, d.month, d.day) + timedelta(hours=day_starts_at)
    return naive.astimezone().astimezone(timezone.utc)


def is_anchored(dt: datetime | None) -> bool:
    if dt is None:
        return True
    u = to_utc(dt)
    return (u.hour, u.minute, u.second, u.microsecond) == (12, 0, 0, 0)


@dataclass
class SchedulerSettings:
    desired_retention: float = 0.90
    maximum_interval: int = 60
    again_next_day: bool = True
    parameters: tuple[float, ...] | None = None   # None = FSRS-6 defaults
    day_starts_at: int = 4

    def anchor(self, when: datetime) -> datetime:
        return day_anchor(study_date(when, self.day_starts_at))

    def build(self, fuzz: bool = True) -> Scheduler:
        extra = {"parameters": tuple(self.parameters)} if self.parameters else {}
        return Scheduler(
            **extra,
            desired_retention=self.desired_retention,
            learning_steps=(),
            relearning_steps=(),
            maximum_interval=self.maximum_interval,
            enable_fuzzing=fuzz,
        )


def validate_parameters(values) -> tuple[float, ...]:
    """Raise ValueError unless `values` is a valid FSRS-6 parameter list (21 numbers in bounds)."""
    if not isinstance(values, (list, tuple)) or len(values) != len(DEFAULT_PARAMETERS):
        raise ValueError(f"parameters must be a list of {len(DEFAULT_PARAMETERS)} numbers")
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            raise ValueError("parameters must be finite numbers")
        out.append(float(v))
    Scheduler(parameters=tuple(out))  # raises ValueError if any value is out of bounds
    return tuple(out)


def new_card() -> Card:
    card = Card()
    card.due = utcnow()
    return card


def is_new(card: Card) -> bool:
    return card.last_review is None


def _apply_again_rule(card: Card, rating: int, anchor: datetime,
                      settings: SchedulerSettings) -> Card:
    if settings.again_next_day and rating == Rating.Again:
        tomorrow = anchor + timedelta(days=1)
        if card.due > tomorrow:
            card.due = tomorrow
    return card


def due_date(card: Card) -> date | None:
    return None if is_new(card) else anchor_date(card.due)


def review(card: Card, rating: int, settings: SchedulerSettings,
           reviewed_at: datetime | None = None,
           duration_ms: int | None = None) -> tuple[Card, ReviewLog]:
    if rating not in RATING_NAMES:
        raise ValueError("rating must be 1 (again), 2 (hard), 3 (good) or 4 (easy)")
    anchor = settings.anchor(reviewed_at or utcnow())
    scheduler = settings.build(fuzz=True)
    new, log = scheduler.review_card(card, Rating(rating), anchor, duration_ms)
    return _apply_again_rule(new, rating, anchor, settings), log


def replay_one(card: Card, rating: int, settings: SchedulerSettings,
               reviewed_at: datetime, duration_ms: int | None = None,
               seed: str | None = None) -> Card:
    """Re-run one past review under (possibly new) settings.

    With a seed, the usual +/- few days of random spread ("fuzz") is applied
    deterministically, so a settings change doesn't bunch problems onto the same day.
    """
    anchor = settings.anchor(reviewed_at)
    if seed is None:
        new, _ = settings.build(fuzz=False).review_card(card, Rating(rating), anchor, duration_ms)
    else:
        saved = random.getstate()
        try:
            random.seed(seed)
            new, _ = settings.build(fuzz=True).review_card(card, Rating(rating), anchor, duration_ms)
        finally:
            random.setstate(saved)
    return _apply_again_rule(new, rating, anchor, settings)


def preview(card: Card, settings: SchedulerSettings,
            at: datetime | None = None) -> dict[str, dict]:
    """What would happen for each rating right now (no fuzz, so it's stable)."""
    anchor = settings.anchor(at or utcnow())
    scheduler = settings.build(fuzz=False)
    out = {}
    for value, name in RATING_NAMES.items():
        new, _ = scheduler.review_card(card, Rating(value), anchor)
        new = _apply_again_rule(new, value, anchor, settings)
        d = anchor_date(new.due)
        out[name] = {
            "rating": value,
            "due": study_day_start(d, settings.day_starts_at).isoformat(),
            "due_date": d.isoformat(),
            "interval_days": (new.due - anchor).days,
            "stability": round(new.stability, 2),
            "difficulty": round(new.difficulty, 2),
        }
    return out


def retrievability(card: Card, settings: SchedulerSettings,
                   at: datetime | None = None) -> float | None:
    """Predicted chance of solving it on the study day containing `at`."""
    if is_new(card):
        return None
    anchor = settings.anchor(at or utcnow())
    return round(settings.build(fuzz=False).get_card_retrievability(card, anchor), 4)


def reschedule(card_id: int, logs: list[ReviewLog],
               settings: SchedulerSettings) -> Card:
    """Replay a problem's whole review history under new settings."""
    if not logs:
        card = new_card()
        card.card_id = card_id
        return card
    logs = sorted(logs, key=lambda l: l.review_datetime)
    card = Card(card_id=card_id, due=settings.anchor(logs[0].review_datetime))
    for i, log in enumerate(logs):
        card = replay_one(card, int(log.rating), settings, log.review_datetime,
                          log.review_duration, seed=f"{card_id}:{i}")  # store seeds by review id
    return card


def interval_preview(settings: SchedulerSettings) -> dict[str, list[float]]:
    """Intervals (days) for a problem you keep rating the same way, for the settings page."""
    scheduler = settings.build(fuzz=False)
    start = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    result = {}
    for label, first, rest in (
        ("good_every_time", Rating.Good, Rating.Good),
        ("hard_first_then_good", Rating.Hard, Rating.Good),
        ("hard_every_time", Rating.Hard, Rating.Hard),
    ):
        card, t, out = Card(), start, []
        for i in range(7):
            card, _ = scheduler.review_card(card, first if i == 0 else rest, t)
            out.append(round((card.due - t).total_seconds() / 86400, 1))
            t = card.due
        result[label] = out
    return result


def card_to_json(card: Card) -> str:
    return card.to_json()


def card_from_json(text: str) -> Card:
    return Card.from_json(text)


__all__ = [
    "Card", "Rating", "ReviewLog", "State", "SchedulerSettings", "RATING_NAMES",
    "utcnow", "to_utc", "parse_dt", "new_card", "is_new", "review", "replay_one", "preview",
    "study_date", "day_anchor", "anchor_date", "study_day_start", "is_anchored", "due_date",
    "retrievability", "reschedule", "interval_preview", "card_to_json", "card_from_json",
]
