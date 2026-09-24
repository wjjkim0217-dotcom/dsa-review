"""Optional: tune the scheduler to YOUR review history (the FSRS optimizer).

FSRS starts with default parameters learned from millions of flashcard reviews.
Once you have a few hundred reviews of your own, the optimizer can fit the 21
parameters to how *you* remember coding problems.

One-time install (large download - it pulls in PyTorch):
    Windows:  .venv\\Scripts\\python -m pip install "fsrs[optimizer]==6.3.2"
    Mac/Linux: .venv/bin/python -m pip install "fsrs[optimizer]==6.3.2"

Run it while the app is closed:
    Windows:  .venv\\Scripts\\python optimize.py
    Mac/Linux: .venv/bin/python optimize.py

To go back to the defaults later, use "Reset to defaults" on the Settings page.
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

import scheduling as sched  # noqa: E402
from fsrs.scheduler import DEFAULT_PARAMETERS  # noqa: E402
from store import Store  # noqa: E402

# py-fsrs silently keeps the defaults unless it sees at least this many reviews
# that happened a day or more after the previous review of the same problem.
MIN_SPACED_REVIEWS = 512


def load_logs(store: Store):
    logs, spaced, last_seen = [], 0, {}
    with store.conn() as c:
        rows = c.execute(
            "SELECT problem_id, rating, reviewed_at, duration_ms FROM reviews ORDER BY reviewed_at, id"
        ).fetchall()
    for r in rows:
        when = sched.parse_dt(r["reviewed_at"])
        prev = last_seen.get(r["problem_id"])
        if prev is not None and when - prev >= timedelta(days=1):
            spaced += 1
        last_seen[r["problem_id"]] = when
        logs.append(sched.ReviewLog(card_id=r["problem_id"], rating=sched.Rating(r["rating"]),
                                    review_datetime=when, review_duration=r["duration_ms"]))
    return logs, spaced


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fit FSRS parameters to your review history")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--yes", action="store_true", help="apply without asking")
    args = parser.parse_args(argv)

    db = Path(args.data_dir).resolve() / "dsa_review.db"
    if not db.exists():
        print(f"No database found at {db}. Start the app and add some problems first.")
        return 1
    store = Store(db)
    logs, spaced = load_logs(store)
    print(f"Reviews in your history: {len(logs)} ({spaced} were a day or more after the previous one)")
    if spaced < MIN_SPACED_REVIEWS:
        print(f"The optimizer needs at least {MIN_SPACED_REVIEWS} spaced reviews to beat the defaults.")
        print(f"You're {MIN_SPACED_REVIEWS - spaced} away - keep practicing and try again later.")
        return 0

    try:
        from fsrs import Optimizer
        optimizer = Optimizer(logs)
    except ImportError:
        print("The optimizer isn't installed. Install it with:")
        print('    .venv\\Scripts\\python -m pip install "fsrs[optimizer]==6.3.2"   (Windows)')
        print('    .venv/bin/python -m pip install "fsrs[optimizer]==6.3.2"        (Mac/Linux)')
        return 1

    print("Fitting parameters (this can take a minute)...")
    params = [round(float(p), 4) for p in optimizer.compute_optimal_parameters()]
    current = store.get_settings().get("fsrs_parameters") or list(DEFAULT_PARAMETERS)
    print("\n  #   current    new")
    for i, (old, new) in enumerate(zip(current, params)):
        print(f"  w{i:<2} {old:9.4f} {new:9.4f}")

    before = sched.interval_preview(store.scheduler_settings())
    trial = dict(store.get_settings(), fsrs_parameters=params)
    after = sched.interval_preview(store.scheduler_settings(trial))
    fmt = lambda xs: " -> ".join(f"{x:g}d" for x in xs[:5])  # noqa: E731
    print("\nIf you rate Good every time, problems come back after:")
    print(f"  now:  {fmt(before['good_every_time'])}")
    print(f"  new:  {fmt(after['good_every_time'])}")

    if not args.yes:
        answer = input("\nApply these parameters and reschedule every problem? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing changed.")
            return 0
    backup = store.backup(db.parent / "backups")
    store.update_settings({"fsrs_parameters": params})
    print(f"Done. Backup saved to {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
