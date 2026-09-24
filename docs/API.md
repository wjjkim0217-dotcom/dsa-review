# DSA Review – local API

Base URL: `http://127.0.0.1:<port>` (default port 8765). All responses are JSON.
Errors come back as `{"error": "message"}` with a 4xx/5xx status.

**Every non-GET request must send `Content-Type: application/json`** (even DELETE and
`/undo`, which have no body – send `{}`). Requests with a foreign `Origin` or `Host`
header are refused (CSRF / DNS-rebinding protection).

## Problem object

```jsonc
{
  "id": 1, "uid": "uuid", "title": "Two Sum",
  "url": "https://leetcode.com/problems/two-sum/",   // "" or http(s) only
  "source": "LeetCode", "difficulty": "Easy",          // "", "Easy", "Medium", "Hard"
  "tags": ["hash-map", "arrays"],                      // lowercase slugs
  "prompt": "problem statement / summary",
  "insight": "one-line key idea",
  "notes": "approach, complexity, edge cases",
  "solution": "code", "language": "python",
  "suspended": false,
  "created_at": "ISO-8601 UTC", "updated_at": "ISO-8601 UTC",

  // scheduling (computed)
  "status": "new" | "due" | "scheduled" | "suspended",
  //   new       = never reviewed (added without solving)
  //   due       = due on or before today's study date (includes overdue)
  //   scheduled = due on a later study date
  // A "study date" starts at settings.day_starts_at (4am local by default), so a review at
  // 1am still counts for the previous day. Scheduling works in whole study days.
  "due": "ISO-8601 UTC" | null,          // when the due study day starts on this computer; null when new
  "due_date": "YYYY-MM-DD" | null,       // the due study date
  "due_in_days": 3 | null,               // due_date - today (negative = overdue)
  "overdue_days": 0 | null,              // max(0, -due_in_days)
  "last_review": "ISO-8601 UTC" | null,  // real time of the latest review
  "stability": 11.0 | null,              // days until predicted recall falls to 90%
  "fsrs_difficulty": 2.1 | null,         // 1 (easy for you) .. 10 (hard for you)
  "retrievability": 0.87 | null,         // predicted chance you can solve it right now
  "reps": 3, "lapses": 1,                // total reviews, times rated Again
  "last_rating": 3 | null
}
```

`GET /api/problems/:id` additionally returns:

```jsonc
"history": [ { "id": 7, "rating": 3, "rating_name": "good",
               "kind": "review" | "added",   // "added" = the rating given when the problem was saved
               "reviewed_at": "ISO", "duration_ms": 900000 | null,
               "next_due": "ISO", "interval_days": 11, "stability": 11.0 } ],   // newest first
"preview": {   // what each button would do if pressed right now
  "again": { "rating": 1, "due": "ISO", "due_date": "YYYY-MM-DD", "interval_days": 1,
             "stability": 0.2, "difficulty": 6.4 },   // no random spread, so the real gap can differ by a few days
  "hard":  { ... }, "good": { ... }, "easy": { ... }
}
```

## Endpoints

| Method & path | Body / query | Returns |
|---|---|---|
| `GET /api/summary` | – | `{now, today (study date), day_start, day_end, counts:{total, active, suspended, new, due, scheduled, reviewed_today}, streak_days, recall_rate_30d (0-1 or null), reviews_30d, forecast:[{date:"YYYY-MM-DD", count}] ×14 (index 0 = today incl. overdue), settings}` |
| `GET /api/queue?tag=` | optional tag filter | `{due:[problem…] (weakest recall first), new:[problem…] (limited by new_per_day), new_waiting, new_left_today, new_introduced_today}` |
| `GET /api/tags` | – | `{tags:[{tag, count}]}` |
| `GET /api/problems?q=&tag=&status=` | filters optional; `q` searches title, insight, source, prompt, notes and tags; `status` ∈ new, due, scheduled, suspended (anything else → 400) | `{problems:[problem…]}` sorted by due |
| `POST /api/problems` | problem fields (`title` required) + optional `first_rating` (1-4 or null) + optional `duration_ms` | 201, full problem (with history/preview). `first_rating` = "I just solved it, here's how it went"; null = add to the new queue |
| `GET /api/problems/:id` | – | full problem |
| `PATCH /api/problems/:id` | any subset of: title, url, source, difficulty, tags, prompt, insight, notes, solution, language, suspended | full problem |
| `DELETE /api/problems/:id` | `{}` | `{deleted: id}` |
| `POST /api/problems/:id/review` | `{rating: 1-4, duration_ms?: int}` | full problem. Works any time (early reviews are fine – FSRS accounts for elapsed time) |
| `POST /api/problems/:id/undo` | `{}` or `{review_id}` | full problem (last review removed, schedule restored). 400 if no reviews, or if `review_id` is given and is no longer the latest review (already undone / rated again since) |
| `GET /api/settings` | – | `{settings, interval_preview}` |
| `PATCH /api/settings` | any of `desired_retention` (0.70-0.99), `maximum_interval` (days, 1-36500), `again_next_day` (bool), `new_per_day` (0-100), `day_starts_at` (hour 0-23), `fsrs_parameters` (null = FSRS-6 defaults, or 21 numbers within FSRS bounds; normally written by `optimize.py`) | `{settings, interval_preview}`; changing anything except `new_per_day` replays every problem's history under the new settings, in the same transaction |
| `GET /api/settings/preview?desired_retention=&maximum_interval=` | unsaved values | `{interval_preview}` |
| `GET /api/export` | – | full backup JSON (served as a download) |
| `POST /api/import` | a backup JSON object | `{added, skipped}` (merge; problems already present by uid are skipped) |

`interval_preview` = `{good_every_time:[days×7], hard_first_then_good:[…], hard_every_time:[…]}` –
the gap before each successive review if you keep rating that way.

Ratings: 1 = Again, 2 = Hard, 3 = Good, 4 = Easy. Numbers are validated strictly: booleans,
fractions, `Infinity`/`NaN` and out-of-range values are rejected with 400. `duration_ms` is
clamped to 0–24 h. On `POST /api/problems`, `first_rating` of `null`, `""` or `0` means "not solved yet".

`POST /api/import` is all-or-nothing for problems (a malformed problem entry → 400 and nothing is
imported); malformed or orphaned review-history entries are skipped. Schedules are kept as-is
when the backup's scheduling settings match this computer's, and recomputed otherwise.
