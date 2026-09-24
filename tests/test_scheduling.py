"""Tests for app/scheduling.py (FSRS-6 wrapper on a study-day clock).

FSRS only ever sees "day anchors": every review made on local study date D (a day
starts at ``day_starts_at``, 4am by default) is fed to FSRS as D 12:00 UTC.
"""
import os
import random
import sys
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import DAY, local, review_state_card, use_timezone, utc  # noqa: E402

import scheduling as sched  # noqa: E402
from fsrs import Card, Rating, ReviewLog, Scheduler, State  # noqa: E402
from fsrs.scheduler import DEFAULT_PARAMETERS  # noqa: E402

D0 = date(2026, 1, 15)
A0 = utc(2026, 1, 15, 12)       # day anchor of D0
T0 = local(2026, 1, 15, 12)     # local noon on D0: study date D0 in every timezone
S = sched.SchedulerSettings


def at_local(d: date, hours: float) -> datetime:
    """Local wall-clock time ``hours`` after midnight of ``d`` (DST-correct)."""
    return (datetime(d.year, d.month, d.day) + timedelta(hours=hours)).astimezone()


def gap(card) -> timedelta:
    return card.due - card.last_review


def raw_fsrs(settings, card, rating, at):
    """What plain FSRS would do on the same day anchor (no again-next-day rule, no fuzz)."""
    new, _ = settings.build(fuzz=False).review_card(card, Rating(rating), settings.anchor(at))
    return new


def strip_id(card: Card) -> dict:
    return {k: v for k, v in card.to_dict().items() if k != "card_id"}


class SeededRandomMixin:
    """Tests that seed the global RNG put it back afterwards."""

    def setUp(self):
        super().setUp()
        self.addCleanup(random.setstate, random.getstate())


class DefaultsTest(unittest.TestCase):
    def test_default_settings(self):
        s = S()
        self.assertEqual((s.desired_retention, s.maximum_interval, s.again_next_day,
                          s.parameters, s.day_starts_at), (0.90, 60, True, None, 4))

    def test_build_has_no_learning_steps(self):
        for fuzz in (True, False):
            scheduler = S().build(fuzz=fuzz)
            self.assertIsInstance(scheduler, Scheduler)
            self.assertEqual(scheduler.learning_steps, ())
            self.assertEqual(scheduler.relearning_steps, ())
            self.assertEqual(scheduler.enable_fuzzing, fuzz)
            self.assertEqual(scheduler.maximum_interval, 60)
            self.assertEqual(scheduler.desired_retention, 0.90)
            self.assertEqual(scheduler.parameters, tuple(DEFAULT_PARAMETERS))

    def test_custom_parameters(self):
        params = list(DEFAULT_PARAMETERS)
        params[2] = 10.0
        self.assertEqual(S(parameters=tuple(params)).build().parameters, tuple(params))
        p = sched.preview(sched.new_card(), S(parameters=tuple(params)), T0)
        self.assertEqual(p["good"]["interval_days"], 10)

    def test_rating_names(self):
        self.assertEqual(sched.RATING_NAMES, {1: "again", 2: "hard", 3: "good", 4: "easy"})


# ============================================================================ study-day clock
class StudyDayClockTest(unittest.TestCase):
    def test_study_date_boundary_at_4am(self):
        cases = (
            (local(2026, 1, 15, 3, 59, 59), date(2026, 1, 14)),
            (local(2026, 1, 15, 4), D0),
            (local(2026, 1, 15, 12), D0),
            (local(2026, 1, 15, 23, 59), D0),
            (local(2026, 1, 16, 0, 0), D0),
            (local(2026, 1, 16, 2), D0),
            (local(2026, 1, 16, 3, 59, 59), D0),
            (local(2026, 1, 16, 4), date(2026, 1, 16)),
        )
        for when, expected in cases:
            with self.subTest(when=when):
                self.assertEqual(sched.study_date(when), expected)
                self.assertEqual(sched.study_date(when, 4), expected)
                self.assertEqual(sched.study_date(when.astimezone(timezone.utc)), expected)

    def test_study_date_other_start_hours(self):
        self.assertEqual(sched.study_date(local(2026, 1, 15, 23, 59), 0), D0)
        self.assertEqual(sched.study_date(local(2026, 1, 16, 0, 0), 0), date(2026, 1, 16))
        self.assertEqual(sched.study_date(local(2026, 1, 15, 22, 59), 23), date(2026, 1, 14))
        self.assertEqual(sched.study_date(local(2026, 1, 15, 23, 0), 23), D0)
        self.assertEqual(sched.study_date(local(2026, 1, 16, 22, 0), 23), D0)

    def test_study_date_rejects_naive(self):
        with self.assertRaises(ValueError):
            sched.study_date(datetime(2026, 1, 15, 12))

    def test_day_anchor_and_inverse(self):
        self.assertEqual(sched.day_anchor(D0), A0)
        self.assertIs(sched.day_anchor(D0).tzinfo, timezone.utc)
        self.assertEqual(sched.anchor_date(A0), D0)
        # tolerant of legacy, non-anchored timestamps (uses the UTC date)
        self.assertEqual(sched.anchor_date(utc(2026, 1, 15, 0, 0)), D0)
        self.assertEqual(sched.anchor_date(utc(2026, 1, 15, 23, 59, 59)), D0)
        seoul = datetime(2026, 1, 16, 5, tzinfo=timezone(timedelta(hours=9)))  # Jan 15 20:00Z
        self.assertEqual(sched.anchor_date(seoul), D0)
        for d in (date(1971, 1, 1), date(2026, 3, 8), date(2026, 11, 1), date(2999, 12, 31)):
            self.assertEqual(sched.anchor_date(sched.day_anchor(d)), d)

    def test_study_day_start(self):
        for hour in (0, 4, 12, 23):
            with self.subTest(hour=hour):
                start = sched.study_day_start(D0, hour)
                self.assertIs(start.tzinfo, timezone.utc)
                self.assertEqual(start, local(2026, 1, 15, hour))
                self.assertEqual(sched.study_date(start, hour), D0)
                self.assertEqual(sched.study_date(start - timedelta(microseconds=1), hour),
                                 date(2026, 1, 14))
        self.assertEqual(sched.study_day_start(D0), local(2026, 1, 15, 4))
        self.assertEqual(sched.study_day_start(date(2026, 1, 16)) - sched.study_day_start(D0), DAY)

    def test_is_anchored(self):
        self.assertTrue(sched.is_anchored(None))
        self.assertTrue(sched.is_anchored(A0))
        self.assertTrue(sched.is_anchored(A0 + 30 * DAY))
        self.assertTrue(sched.is_anchored(A0.astimezone(timezone(timedelta(hours=9)))))
        self.assertFalse(sched.is_anchored(A0 + timedelta(seconds=1)))
        self.assertFalse(sched.is_anchored(A0 + timedelta(microseconds=1)))
        self.assertFalse(sched.is_anchored(utc(2026, 1, 15, 0)))
        self.assertFalse(sched.is_anchored(datetime(2026, 1, 15, 12, tzinfo=timezone(timedelta(hours=9)))))

    def test_due_date(self):
        self.assertIsNone(sched.due_date(sched.new_card()))
        card, _ = sched.review(sched.new_card(), 3, S(), T0)
        self.assertEqual(sched.due_date(card), D0 + timedelta(days=2))

    def test_settings_anchor_uses_day_starts_at(self):
        five_am = local(2026, 1, 15, 5)
        self.assertEqual(S().anchor(five_am), A0)
        self.assertEqual(S(day_starts_at=6).anchor(five_am), A0 - DAY)
        self.assertEqual(S(day_starts_at=0).anchor(local(2026, 1, 16, 2)), A0 + DAY)
        self.assertEqual(S().anchor(local(2026, 1, 16, 2)), A0)

    def test_late_evening_and_after_midnight_share_a_study_date(self):
        eleven_pm, two_am = local(2026, 1, 15, 23), local(2026, 1, 16, 2)
        self.assertEqual(sched.study_date(eleven_pm), sched.study_date(two_am))
        self.assertEqual(S().anchor(eleven_pm), S().anchor(two_am))
        card, _ = sched.review(sched.new_card(), 3, S(), eleven_pm)
        self.assertEqual(card.last_review, A0)
        self.assertEqual(sched.retrievability(card, S(), two_am), 1.0)
        self.assertEqual(sched.preview(card, S(), two_am), sched.preview(card, S(), eleven_pm))
        # ... but with a midnight day start they are different study days
        self.assertNotEqual(S(day_starts_at=0).anchor(eleven_pm), S(day_starts_at=0).anchor(two_am))
        self.assertLess(sched.retrievability(card, S(day_starts_at=0), two_am), 1.0)


# ============================================================================ previews / reviews
class NewCardPreviewTest(SeededRandomMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.card = sched.new_card()
        self.preview = sched.preview(self.card, S(), T0)

    def test_new_card_is_new_and_due_now(self):
        self.assertTrue(sched.is_new(self.card))
        self.assertIsNone(self.card.last_review)
        self.assertEqual(self.card.due.tzinfo, timezone.utc)
        self.assertLess(abs((self.card.due - sched.utcnow()).total_seconds()), 60)

    def test_default_intervals(self):
        got = {name: p["interval_days"] for name, p in self.preview.items()}
        self.assertEqual(got, {"again": 1, "hard": 1, "good": 2, "easy": 8})
        for p in self.preview.values():
            self.assertIs(type(p["interval_days"]), int)

    def test_preview_shape(self):
        self.assertEqual(list(self.preview), ["again", "hard", "good", "easy"])
        for value, name in sched.RATING_NAMES.items():
            p = self.preview[name]
            self.assertEqual(set(p), {"rating", "due", "due_date", "interval_days",
                                      "stability", "difficulty"})
            self.assertEqual(p["rating"], value)
            due_d = D0 + timedelta(days=p["interval_days"])
            self.assertEqual(p["due_date"], due_d.isoformat())
            # `due` is the real instant that study day starts (4am local)
            self.assertEqual(p["due"], sched.study_day_start(due_d).isoformat())
            self.assertEqual(datetime.fromisoformat(p["due"]),
                             local(due_d.year, due_d.month, due_d.day, 4))
            self.assertTrue(1.0 <= p["difficulty"] <= 10.0)

    def test_preview_due_follows_day_starts_at(self):
        p = sched.preview(self.card, S(day_starts_at=7), T0)["good"]
        self.assertEqual(p["due"], local(2026, 1, 17, 7).astimezone(timezone.utc).isoformat())
        self.assertEqual(p["due_date"], "2026-01-17")

    def test_preview_same_for_any_time_of_the_study_day(self):
        for when in (local(2026, 1, 15, 4), local(2026, 1, 15, 23, 59), local(2026, 1, 16, 3, 59)):
            self.assertEqual(sched.preview(self.card, S(), when), self.preview)

    def test_initial_stability_is_fsrs6_default(self):
        for value, name in sched.RATING_NAMES.items():
            self.assertAlmostEqual(self.preview[name]["stability"],
                                   DEFAULT_PARAMETERS[value - 1], places=2)

    def test_difficulty_ordering(self):
        p = self.preview
        self.assertGreater(p["again"]["difficulty"], p["hard"]["difficulty"])
        self.assertGreater(p["hard"]["difficulty"], p["good"]["difficulty"])
        self.assertGreater(p["good"]["difficulty"], p["easy"]["difficulty"])

    def test_preview_does_not_mutate_card(self):
        before = self.card.to_dict()
        sched.preview(self.card, S(), T0)
        self.assertEqual(self.card.to_dict(), before)

    def test_preview_is_stable(self):
        self.assertEqual(sched.preview(self.card, S(), T0), self.preview)

    def test_no_same_day_steps(self):
        """Every rating on a new card goes straight to Review with >= 1 day interval."""
        for rating in sched.RATING_NAMES:
            for _ in range(5):
                card, log = sched.review(sched.new_card(), rating, S(), T0)
                self.assertEqual(card.state, State.Review)
                self.assertIsNone(card.step)
                self.assertGreaterEqual(gap(card), DAY)
                self.assertEqual(card.last_review, A0)
                self.assertEqual(int(log.rating), rating)

    def test_reviews_store_day_anchors(self):
        for when in (local(2026, 1, 15, 4), T0, local(2026, 1, 16, 3, 30)):
            card, log = sched.review(sched.new_card(), 4, S(), when)
            self.assertEqual(card.last_review, A0)
            self.assertEqual(log.review_datetime, A0)
            self.assertTrue(sched.is_anchored(card.due))
            self.assertEqual(gap(card) % DAY, timedelta(0))

    def test_real_review_exact_short_intervals(self):
        # Intervals under 2.5 days are never fuzzed, so these are exact.
        for rating, days in ((1, 1), (2, 1), (3, 2)):
            card, _ = sched.review(sched.new_card(), rating, S(), T0)
            self.assertEqual(card.due - A0, timedelta(days=days), rating)

    def test_real_review_easy_is_fuzzed_around_8_days(self):
        seen = set()
        for i in range(40):
            random.seed(i)
            card, _ = sched.review(sched.new_card(), 4, S(), T0)
            self.assertTrue(5 <= gap(card).days <= 11, gap(card))
            self.assertEqual(gap(card) % DAY, timedelta(0))  # whole days
            seen.add(gap(card).days)
        self.assertGreater(len(seen), 1)  # fuzz really is applied

    def test_review_keeps_duration_in_log(self):
        _, log = sched.review(sched.new_card(), 3, S(), T0, duration_ms=1234)
        self.assertEqual(log.review_duration, 1234)
        self.assertEqual(log.review_datetime, A0)

    def test_review_defaults_to_now(self):
        before = S().anchor(sched.utcnow())
        card, _ = sched.review(sched.new_card(), 3, S())
        after = S().anchor(sched.utcnow())
        self.assertIn(card.last_review, {before, after})

    def test_invalid_ratings_rejected(self):
        for bad in (0, 5, -1, 99):
            with self.assertRaises(ValueError):
                sched.review(sched.new_card(), bad, S(), T0)

    def test_review_does_not_mutate_input(self):
        card = sched.new_card()
        before = card.to_dict()
        sched.review(card, 3, S(), T0)
        self.assertEqual(card.to_dict(), before)


class AgainRuleTest(unittest.TestCase):
    """again_next_day=True caps "Again" at one day; False lets FSRS decide."""

    def strong_card(self):
        # Very stable card reviewed 30 days ago: plain FSRS would give "Again" a 3-day interval.
        return review_state_card(last_review=A0 - 30 * DAY, stability=100.0, difficulty=5.0, due=A0)

    def test_fsrs_alone_would_exceed_one_day(self):
        raw = raw_fsrs(S(), self.strong_card(), 1, T0)
        self.assertGreater(raw.due - A0, DAY)

    def test_again_within_one_day_when_enabled_preview(self):
        cards = [sched.new_card(), self.strong_card()] + [
            review_state_card(last_review=A0 - timedelta(days=d), stability=s, due=A0)
            for s, d in ((0.5, 1), (3, 3), (30, 45), (1000, 400), (36500, 10))
        ]
        for card in cards:
            p = sched.preview(card, S(again_next_day=True), T0)["again"]
            self.assertEqual(p["interval_days"], 1, card)
            self.assertEqual(p["due_date"], "2026-01-16")

    def test_again_within_one_day_when_enabled_real_review(self):
        for _ in range(20):
            card, _ = sched.review(self.strong_card(), 1, S(again_next_day=True), T0)
            self.assertEqual(card.due - A0, DAY)
            self.assertEqual(card.state, State.Review)  # no relearning steps

    def test_again_rule_only_shortens_due_not_memory_state(self):
        capped = sched.preview(self.strong_card(), S(again_next_day=True), T0)["again"]
        raw = raw_fsrs(S(), self.strong_card(), 1, T0)
        self.assertEqual(capped["stability"], round(raw.stability, 2))
        self.assertEqual(capped["difficulty"], round(raw.difficulty, 2))

    def test_again_follows_fsrs_when_disabled(self):
        settings = S(again_next_day=False)
        p = sched.preview(self.strong_card(), settings, T0)["again"]
        raw = raw_fsrs(settings, self.strong_card(), 1, T0)
        self.assertEqual(p["interval_days"], (raw.due - A0).days)
        self.assertGreater(p["interval_days"], 1)
        for _ in range(20):
            card, _ = sched.review(self.strong_card(), 1, settings, T0)
            self.assertGreater(card.due - A0, DAY)

    def test_again_rule_does_not_touch_other_ratings(self):
        card = self.strong_card()
        on = sched.preview(card, S(again_next_day=True), T0)
        off = sched.preview(card, S(again_next_day=False), T0)
        for name in ("hard", "good", "easy"):
            self.assertEqual(on[name], off[name])


class MaximumIntervalTest(unittest.TestCase):
    def test_preview_capped(self):
        p = sched.preview(sched.new_card(), S(maximum_interval=5), T0)
        self.assertEqual(p["easy"]["interval_days"], 5)
        self.assertEqual(p["good"]["interval_days"], 2)

    def test_maximum_interval_one_day(self):
        p = sched.preview(sched.new_card(), S(maximum_interval=1), T0)
        self.assertEqual({v["interval_days"] for v in p.values()}, {1})

    def test_real_reviews_never_exceed_cap(self):
        for cap in (7, 30, 60):
            settings = S(maximum_interval=cap)
            for rating in (3, 4):
                card, t = sched.new_card(), T0
                for _ in range(12):
                    card, _ = sched.review(card, rating, settings, t)
                    days = gap(card).days
                    self.assertTrue(1 <= days <= cap, (cap, rating, days))
                    t = at_local(sched.due_date(card), 12)  # review on the due date
                # Long chains of successful reviews end up at (fuzzed) cap.
                self.assertGreaterEqual(days, 0.7 * cap, (cap, rating))

    def test_default_cap_is_60_days(self):
        card = review_state_card(last_review=A0 - 400 * DAY, stability=5000.0, due=A0)
        p = sched.preview(card, S(), T0)
        self.assertEqual(p["easy"]["interval_days"], 60)
        self.assertEqual(p["good"]["interval_days"], 60)


class DesiredRetentionTest(unittest.TestCase):
    def test_higher_retention_gives_shorter_intervals_new_card(self):
        easy = [sched.preview(sched.new_card(), S(desired_retention=r, maximum_interval=36500),
                              T0)["easy"]["interval_days"] for r in (0.75, 0.85, 0.90, 0.95, 0.97)]
        self.assertEqual(easy, sorted(easy, reverse=True))
        self.assertGreater(easy[0], easy[-1])
        self.assertEqual(easy[2], 8)

    def test_higher_retention_gives_shorter_intervals_review_card(self):
        card = review_state_card(last_review=A0 - 20 * DAY, stability=20.0, due=A0)
        good = [sched.preview(card, S(desired_retention=r, maximum_interval=36500), T0)
                ["good"]["interval_days"] for r in (0.70, 0.80, 0.90, 0.95, 0.99)]
        for longer, shorter in zip(good, good[1:]):
            self.assertGreater(longer, shorter)

    def test_interval_preview_totals_shrink(self):
        def total(r):
            return sum(sched.interval_preview(
                S(desired_retention=r, maximum_interval=36500))["good_every_time"])
        self.assertGreater(total(0.80), total(0.90))
        self.assertGreater(total(0.90), total(0.97))


class RetrievabilityTest(unittest.TestCase):
    def test_none_for_new_card(self):
        self.assertIsNone(sched.retrievability(sched.new_card(), S()))
        self.assertIsNone(sched.retrievability(sched.new_card(), S(), T0))

    def test_range_and_monotonic_decay(self):
        card, _ = sched.review(sched.new_card(), 4, S(), T0)
        values = [sched.retrievability(card, S(), at_local(D0 + timedelta(days=d), 12))
                  for d in (0, 1, 2, 8, 30, 365, 3650)]
        for v in values:
            self.assertIsInstance(v, float)
            self.assertGreater(v, 0.0)
            self.assertLessEqual(v, 1.0)
        self.assertEqual(values[0], 1.0)
        for a, b in zip(values, values[1:]):
            self.assertGreater(a, b)

    def test_same_study_day_is_one(self):
        card, _ = sched.review(sched.new_card(), 1, S(), local(2026, 1, 15, 4, 0))
        for when in (local(2026, 1, 15, 23, 59), local(2026, 1, 16, 3, 59, 59)):
            self.assertEqual(sched.retrievability(card, S(), when), 1.0)
        # one second later it is the next study day: one full day has elapsed
        next_day = sched.retrievability(card, S(), local(2026, 1, 16, 4, 0))
        self.assertLess(next_day, 1.0)
        self.assertEqual(next_day, sched.retrievability(card, S(), local(2026, 1, 16, 23, 0)))

    def test_about_desired_retention_after_stability_days(self):
        # Stability S = days until recall probability falls to 90%.
        card = review_state_card(last_review=A0, stability=10.0, due=A0 + 10 * DAY)
        self.assertAlmostEqual(sched.retrievability(card, S(), T0 + 10 * DAY), 0.9, places=3)

    def test_rounded_to_four_places(self):
        card = review_state_card(last_review=A0, stability=3.3, due=A0 + 3 * DAY)
        r = sched.retrievability(card, S(), T0 + 7 * DAY)
        self.assertEqual(r, round(r, 4))

    def test_before_last_review_is_one(self):
        card = review_state_card(last_review=A0, stability=3.0)
        self.assertEqual(sched.retrievability(card, S(), T0 - 5 * DAY), 1.0)

    def test_defaults_to_now(self):
        card = review_state_card(last_review=S().anchor(sched.utcnow()) - 5 * DAY, stability=3.0)
        r = sched.retrievability(card, S())
        self.assertTrue(0 < r < 1)


# ============================================================================ regression: 23h gaps
class DayGapRegressionTest(SeededRandomMixin, unittest.TestCase):
    """py-fsrs counts elapsed time in whole 24-hour blocks. A problem due "tomorrow"
    but practiced 23 hours later used to count as a same-day repeat and loop daily.
    Elapsed time is now measured in study days."""

    RATINGS = (1, 3, 3, 3, 3, 3)   # couldn't solve it, then Good five times

    def run_sequence(self, hour_step):
        """Review on each due date, ``hour_step`` hours later/earlier in the day than before.

        Starts at 20:00 local. With -1 every gap is N days minus an hour (the first one
        is 23h); with +1 every gap is N days plus an hour (25h), and the later reviews
        happen after midnight - still on the due study date.
        """
        card, when = sched.new_card(), local(2026, 1, 15, 20)
        cards, times = [], []
        for k, rating in enumerate(self.RATINGS):
            random.seed(k)  # the same fuzz draw for both sequences
            card, _ = sched.review(card, rating, S(), when)
            cards.append(card)
            times.append(when)
            when = at_local(sched.due_date(card), 20 + hour_step * (k + 1))
        return cards, times

    def test_23h_and_25h_gaps_both_count_as_one_day(self):
        early, early_times = self.run_sequence(-1)
        late, late_times = self.run_sequence(+1)
        self.assertEqual(early_times[1] - early_times[0], timedelta(hours=23))
        self.assertEqual(late_times[1] - late_times[0], timedelta(hours=25))
        # identical memory state and schedule at every step
        self.assertEqual([strip_id(c) for c in early], [strip_id(c) for c in late])
        # the second review is one full study day after the Again
        self.assertEqual(early[1].last_review - early[0].last_review, DAY)

    def test_growth_beyond_one_day(self):
        cards, times = self.run_sequence(-1)
        intervals = [gap(c).days for c in cards]
        self.assertEqual(intervals[:2], [1, 2])   # Again -> 1 day, then Good -> 2 days (unfuzzed)
        for shorter, longer in zip(intervals[1:], intervals[2:]):
            self.assertGreater(longer, shorter)
        self.assertGreaterEqual(intervals[-1], 20)
        stabilities = [c.stability for c in cards]
        self.assertEqual(stabilities, sorted(stabilities))
        for c in cards:
            self.assertTrue(sched.is_anchored(c.last_review))
            self.assertTrue(sched.is_anchored(c.due))

    def test_after_midnight_reviews_stay_on_the_due_study_date(self):
        cards, times = self.run_sequence(+1)
        self.assertTrue(any(t.date() != sched.anchor_date(c.last_review)
                            for t, c in zip(times, cards)))  # some happened after midnight
        for t, c in zip(times, cards):
            self.assertEqual(sched.study_date(t), sched.anchor_date(c.last_review))
        for prev, t in zip(cards, times[1:]):
            self.assertEqual(sched.study_date(t), sched.due_date(prev))

    def test_same_growth_as_exactly_24h_later(self):
        early, _ = self.run_sequence(-1)
        exact, _ = self.run_sequence(0)
        self.assertEqual([strip_id(c) for c in early], [strip_id(c) for c in exact])

    def test_raw_fsrs_would_have_treated_23h_as_same_day(self):
        # Documents the underlying behaviour the day anchors protect against.
        scheduler = S().build(fuzz=False)
        start = utc(2026, 1, 16, 1)   # 20:00 in New York
        again, _ = scheduler.review_card(Card(), Rating.Again, start)
        after_23h, _ = scheduler.review_card(again, Rating.Good, start + timedelta(hours=23))
        after_25h, _ = scheduler.review_card(again, Rating.Good, start + timedelta(hours=25))
        self.assertLess(after_23h.stability, after_25h.stability)
        self.assertEqual((after_23h.due - after_23h.last_review).days, 1)
        # Through the wrapper both give the 25h result.
        card, _ = sched.review(sched.new_card(), 1, S(), local(2026, 1, 15, 20))
        for hours in (23, 25):
            nxt, _ = sched.review(card, 3, S(), local(2026, 1, 15, 20) + timedelta(hours=hours))
            self.assertAlmostEqual(nxt.stability, after_25h.stability)
            self.assertEqual(gap(nxt), 2 * DAY)


# ============================================================================ DST
class DaylightSavingTest(unittest.TestCase):
    """US Eastern time: DST starts 2026-03-08 02:00, ends 2026-11-01 02:00."""

    def setUp(self):
        super().setUp()
        use_timezone(self, "America/New_York")

    def test_study_day_starts_follow_local_clock(self):
        starts = {d: sched.study_day_start(date(2026, *d)) for d in
                  ((3, 7), (3, 8), (3, 9), (10, 31), (11, 1), (11, 2))}
        self.assertEqual(starts[(3, 7)], utc(2026, 3, 7, 9))     # 04:00 EST
        self.assertEqual(starts[(3, 8)], utc(2026, 3, 8, 8))     # 04:00 EDT
        self.assertEqual(starts[(10, 31)], utc(2026, 10, 31, 8))  # 04:00 EDT
        self.assertEqual(starts[(11, 1)], utc(2026, 11, 1, 9))   # 04:00 EST
        self.assertEqual(starts[(3, 8)] - starts[(3, 7)], timedelta(hours=23))
        self.assertEqual(starts[(3, 9)] - starts[(3, 8)], timedelta(hours=24))
        self.assertEqual(starts[(11, 1)] - starts[(10, 31)], timedelta(hours=25))
        self.assertEqual(starts[(11, 2)] - starts[(11, 1)], timedelta(hours=24))

    def test_study_dates_around_fall_back(self):
        cases = (
            (utc(2026, 10, 31, 7, 59), date(2026, 10, 30)),   # 03:59 EDT
            (utc(2026, 10, 31, 8, 0), date(2026, 10, 31)),    # 04:00 EDT
            (utc(2026, 11, 1, 5, 30), date(2026, 10, 31)),    # 01:30 EDT
            (utc(2026, 11, 1, 6, 30), date(2026, 10, 31)),    # 01:30 EST (repeated hour)
            (utc(2026, 11, 1, 8, 59), date(2026, 10, 31)),    # 03:59 EST
            (utc(2026, 11, 1, 9, 0), date(2026, 11, 1)),      # 04:00 EST
        )
        for when, expected in cases:
            with self.subTest(when=when):
                self.assertEqual(sched.study_date(when), expected)

    def test_study_dates_around_spring_forward(self):
        self.assertEqual(sched.study_date(utc(2026, 3, 8, 7, 59)), date(2026, 3, 7))  # 03:59 EDT
        self.assertEqual(sched.study_date(utc(2026, 3, 8, 8, 0)), date(2026, 3, 8))   # 04:00 EDT

    def test_intervals_across_fall_back_are_whole_days(self):
        first_at = utc(2026, 10, 31, 0)            # Fri Oct 30, 20:00 EDT
        card, _ = sched.review(sched.new_card(), 3, S(), first_at)
        self.assertEqual(card.last_review, utc(2026, 10, 30, 12))
        self.assertEqual(card.due, utc(2026, 11, 1, 12))
        p = sched.preview(sched.new_card(), S(), first_at)["good"]
        self.assertEqual((p["interval_days"], p["due_date"], p["due"]),
                         (2, "2026-11-01", "2026-11-01T09:00:00+00:00"))

        second_at = utc(2026, 11, 2, 3)            # Sun Nov 1, 22:00 EST (50 real hours later)
        self.assertEqual(second_at - first_at, timedelta(hours=51))
        self.assertEqual(sched.retrievability(card, S(), second_at),
                         round(S().build().get_card_retrievability(card, utc(2026, 11, 1, 12)), 4))
        card2, _ = sched.review(card, 3, S(), second_at)
        self.assertEqual(card2.last_review, utc(2026, 11, 1, 12))
        self.assertEqual(gap(card2) % DAY, timedelta(0))
        expected = raw_fsrs(S(), card, 3, second_at)
        self.assertEqual(card2.stability, expected.stability)

        pre = sched.preview(card2, S(), second_at)
        for p in pre.values():
            d = date.fromisoformat(p["due_date"])
            self.assertEqual(d - date(2026, 11, 1), timedelta(days=p["interval_days"]))
            self.assertEqual(datetime.fromisoformat(p["due"]),
                             (datetime(d.year, d.month, d.day, 4)).astimezone())

    def test_spring_forward_23h_day_still_counts_as_one_day(self):
        first_at = utc(2026, 3, 8, 2)              # Sat Mar 7, 21:00 EST
        second_at = utc(2026, 3, 9, 1)             # Sun Mar 8, 21:00 EDT (23 real hours later)
        card, _ = sched.review(sched.new_card(), 1, S(), first_at)
        self.assertEqual(card.last_review, utc(2026, 3, 7, 12))
        self.assertLess(sched.retrievability(card, S(), second_at), 1.0)
        card2, _ = sched.review(card, 3, S(), second_at)
        self.assertEqual(card2.last_review - card.last_review, DAY)
        self.assertEqual(gap(card2), 2 * DAY)


# ============================================================================ reschedule / replay
def _logs(card_id, pairs):
    return [ReviewLog(card_id=card_id, rating=Rating(r), review_datetime=t, review_duration=None)
            for r, t in pairs]


def _manual_replay(settings, card_id, pairs):
    """Unfuzzed replay on day anchors."""
    scheduler = settings.build(fuzz=False)
    card = Card(card_id=card_id, due=settings.anchor(pairs[0][1]))
    for rating, t in pairs:
        card, _ = scheduler.review_card(card, Rating(rating), settings.anchor(t))
    return card


class RescheduleTest(SeededRandomMixin, unittest.TestCase):
    HISTORY = [(3, T0), (3, T0 + 2 * DAY), (3, T0 + 13 * DAY)]
    LAST = A0 + 13 * DAY

    def test_empty_history_gives_new_card(self):
        card = sched.reschedule(42, [], S())
        self.assertTrue(sched.is_new(card))
        self.assertEqual(card.card_id, 42)
        self.assertIsNone(card.stability)

    def test_replays_history(self):
        card = sched.reschedule(7, _logs(7, self.HISTORY), S())
        expected = _manual_replay(S(), 7, self.HISTORY)
        self.assertEqual(card.card_id, 7)
        self.assertEqual(card.last_review, self.LAST)
        self.assertEqual(card.state, State.Review)
        # fuzz only moves the due date; memory state is exactly the unfuzzed replay
        self.assertEqual((card.stability, card.difficulty), (expected.stability, expected.difficulty))
        self.assertEqual(gap(expected), 46 * DAY)  # good_every_time: 2, 11, 46
        self.assertEqual(gap(card) % DAY, timedelta(0))
        self.assertTrue(41 <= gap(card).days <= 51, gap(card))

    def test_short_intervals_match_unfuzzed_replay(self):
        for rating, days in ((1, 1), (2, 1), (3, 2)):
            with self.subTest(rating=rating):
                pairs = [(rating, T0)]
                card = sched.reschedule(7, _logs(7, pairs), S())
                self.assertEqual(card.to_dict(), _manual_replay(S(), 7, pairs).to_dict())
                self.assertEqual(card.due, A0 + timedelta(days=days))

    def test_uses_day_anchors_of_real_timestamps(self):
        pairs = [(3, local(2026, 1, 15, 20, 17)), (3, local(2026, 1, 18, 2, 5))]
        card = sched.reschedule(7, _logs(7, pairs), S())
        self.assertEqual(card.last_review, A0 + 2 * DAY)   # 02:05 on the 18th is study date 17th
        card0 = sched.reschedule(7, _logs(7, pairs), S(day_starts_at=0))
        self.assertEqual(card0.last_review, A0 + 3 * DAY)

    def test_order_of_logs_does_not_matter(self):
        logs = _logs(7, self.HISTORY)
        a = sched.reschedule(7, logs, S())
        b = sched.reschedule(7, list(reversed(logs)), S())
        self.assertEqual(a.to_dict(), b.to_dict())

    def test_seeded_replay_is_deterministic(self):
        logs = _logs(7, self.HISTORY)
        results = []
        for i in range(3):
            random.seed(i)   # the global RNG state must not matter
            results.append(sched.reschedule(7, logs, S()).to_dict())
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_seeded_replay_leaves_global_random_alone(self):
        random.seed(99)
        state = random.getstate()
        sched.reschedule(7, _logs(7, self.HISTORY), S())
        sched.replay_one(sched.new_card(), 4, S(), T0, seed="x")
        self.assertEqual(random.getstate(), state)

    def test_replay_one(self):
        new = sched.new_card()
        unfuzzed = sched.replay_one(new, 4, S(), T0)
        self.assertEqual(unfuzzed.due, A0 + 8 * DAY)
        self.assertEqual(unfuzzed.last_review, A0)
        self.assertEqual(sched.replay_one(new, 4, S(), T0, seed="a").to_dict(),
                         sched.replay_one(new, 4, S(), T0, seed="a").to_dict())
        fuzzed = {sched.replay_one(new, 4, S(), T0, seed=str(i)).due for i in range(30)}
        self.assertGreater(len(fuzzed), 1)   # the seed really spreads due dates
        for due in fuzzed:
            self.assertTrue(5 <= (due - A0).days <= 11)
        self.assertEqual(sched.replay_one(new, 1, S(), T0, seed="a").due, A0 + DAY)

    def test_respects_maximum_interval(self):
        card = sched.reschedule(7, _logs(7, self.HISTORY), S(maximum_interval=5))
        self.assertTrue(4 <= gap(card).days <= 5, gap(card))

    def test_respects_desired_retention(self):
        logs = _logs(7, self.HISTORY)
        gaps = [gap(sched.reschedule(7, logs, S(desired_retention=r, maximum_interval=36500)))
                for r in (0.80, 0.90, 0.97)]
        self.assertGreater(gaps[0], gaps[1])
        self.assertGreater(gaps[1], gaps[2])

    def test_memory_state_depends_only_on_history_not_on_cap(self):
        logs = _logs(7, self.HISTORY)
        a = sched.reschedule(7, logs, S(maximum_interval=5))
        b = sched.reschedule(7, logs, S(maximum_interval=36500))
        self.assertNotEqual(a.due, b.due)
        # The cap changes when reviews are *scheduled*, but the replay uses the
        # actual review days, so stability/difficulty are identical.
        self.assertAlmostEqual(a.stability, b.stability)
        self.assertAlmostEqual(a.difficulty, b.difficulty)

    def test_last_again_respects_again_next_day(self):
        history = self.HISTORY + [(1, T0 + 59 * DAY)]
        logs = _logs(7, history)
        last = A0 + 59 * DAY
        on = sched.reschedule(7, logs, S(again_next_day=True))
        self.assertEqual(on.due - last, DAY)
        off = sched.reschedule(7, logs, S(again_next_day=False))
        expected = _manual_replay(S(), 7, history)
        self.assertEqual(off.stability, expected.stability)
        self.assertGreater(gap(expected), DAY)
        self.assertGreaterEqual(off.due - last, 2 * DAY)
        self.assertEqual(on.stability, off.stability)

    def test_earlier_again_not_affected_by_rule(self):
        history = [(3, T0), (1, T0 + 2 * DAY), (3, T0 + 3 * DAY)]
        on = sched.reschedule(7, _logs(7, history), S(again_next_day=True))
        off = sched.reschedule(7, _logs(7, history), S(again_next_day=False))
        self.assertEqual(on.to_dict(), off.to_dict())


class IntervalPreviewTest(unittest.TestCase):
    KEYS = {"good_every_time", "hard_first_then_good", "hard_every_time"}

    def test_shape(self):
        result = sched.interval_preview(S())
        self.assertEqual(set(result), self.KEYS)
        for key, values in result.items():
            self.assertEqual(len(values), 7, key)
            for v in values:
                self.assertIsInstance(v, float)
                self.assertTrue(1.0 <= v <= 60.0, (key, v))

    def test_default_values(self):
        result = sched.interval_preview(S())
        self.assertEqual(result["good_every_time"][:3], [2.0, 11.0, 46.0])
        self.assertEqual(result["hard_first_then_good"][0], 1.0)
        self.assertEqual(result["hard_every_time"][0], 1.0)

    def test_monotonic_and_ordered(self):
        result = sched.interval_preview(S())
        for key, values in result.items():
            self.assertEqual(values, sorted(values), key)
        for good, hard in zip(result["good_every_time"], result["hard_every_time"]):
            self.assertGreaterEqual(good, hard)
        for mixed, hard in zip(result["hard_first_then_good"], result["hard_every_time"]):
            self.assertGreaterEqual(mixed, hard)

    def test_respects_maximum_interval(self):
        result = sched.interval_preview(S(maximum_interval=10))
        for key, values in result.items():
            self.assertLessEqual(max(values), 10.0, key)
            self.assertEqual(values[-1], 10.0, key)

    def test_deterministic(self):
        self.assertEqual(sched.interval_preview(S()), sched.interval_preview(S()))


class DatetimeTest(unittest.TestCase):
    NAIVE = datetime(2026, 1, 15, 12)

    def test_utcnow_is_aware_utc(self):
        self.assertIs(sched.utcnow().tzinfo, timezone.utc)

    def test_to_utc_rejects_naive(self):
        with self.assertRaises(ValueError):
            sched.to_utc(self.NAIVE)

    def test_to_utc_converts_offsets(self):
        seoul = datetime(2026, 1, 15, 21, tzinfo=timezone(timedelta(hours=9)))
        converted = sched.to_utc(seoul)
        self.assertIs(converted.tzinfo, timezone.utc)
        self.assertEqual(converted, A0)
        self.assertEqual(converted.hour, 12)

    def test_parse_dt(self):
        self.assertIsNone(sched.parse_dt(None))
        self.assertIsNone(sched.parse_dt(""))
        self.assertEqual(sched.parse_dt("2026-01-15T12:00:00+00:00"), A0)
        self.assertEqual(sched.parse_dt("2026-01-15T07:00:00-05:00"), A0)
        self.assertIs(sched.parse_dt("2026-01-15T12:00:00+00:00").tzinfo, timezone.utc)

    def test_parse_dt_rejects_naive(self):
        with self.assertRaises(ValueError):
            sched.parse_dt("2026-01-15T12:00:00")

    def test_review_rejects_naive(self):
        with self.assertRaises(ValueError):
            sched.review(sched.new_card(), 3, S(), self.NAIVE)

    def test_preview_rejects_naive(self):
        with self.assertRaises(ValueError):
            sched.preview(sched.new_card(), S(), self.NAIVE)

    def test_retrievability_rejects_naive(self):
        card = review_state_card(last_review=A0, stability=3.0)
        with self.assertRaises(ValueError):
            sched.retrievability(card, S(), self.NAIVE)

    def test_review_accepts_non_utc_aware(self):
        at = datetime(2026, 1, 15, 21, tzinfo=timezone(timedelta(hours=9)))
        card, log = sched.review(sched.new_card(), 3, S(), at)
        expected = sched.day_anchor(sched.study_date(at))
        self.assertEqual(card.last_review, expected)
        self.assertIs(card.last_review.tzinfo, timezone.utc)
        self.assertEqual(card.due, expected + 2 * DAY)
        self.assertIs(log.review_datetime.tzinfo, timezone.utc)

    def test_preview_accepts_non_utc_aware(self):
        at = datetime(2026, 1, 15, 4, tzinfo=timezone(timedelta(hours=-8)))
        self.assertEqual(sched.preview(sched.new_card(), S(), at),
                         sched.preview(sched.new_card(), S(), at.astimezone(timezone.utc)))


class CardJsonTest(unittest.TestCase):
    def test_round_trip_new_card(self):
        card = sched.new_card()
        back = sched.card_from_json(sched.card_to_json(card))
        self.assertEqual(back.to_dict(), card.to_dict())
        self.assertTrue(sched.is_new(back))

    def test_round_trip_reviewed_card(self):
        card, _ = sched.review(sched.new_card(), 1, S(), T0)
        back = sched.card_from_json(sched.card_to_json(card))
        self.assertEqual(back.to_dict(), card.to_dict())
        self.assertFalse(sched.is_new(back))
        self.assertEqual(back.due, card.due)
        self.assertEqual(back.last_review, card.last_review)
        self.assertEqual(back.state, State.Review)
        self.assertEqual(sched.preview(back, S(), T0 + DAY), sched.preview(card, S(), T0 + DAY))


if __name__ == "__main__":
    unittest.main()
