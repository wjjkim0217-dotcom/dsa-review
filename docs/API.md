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
  "deck": "main" | "neetcode",       // which review deck this problem belongs to
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

## Deck scope (`deck=` query param)

`GET /api/problems`, `GET /api/queue`, `GET /api/summary` and `GET /api/tags` all take an
optional `deck` query param that scopes them to one deck or both:

- `main` (the default, same as omitting it) – the main deck, **plus** the NeetCode deck when
  the `neetcode_in_main` setting is on.
- `neetcode` – the NeetCode deck only, regardless of `neetcode_in_main`.
- `all` – both decks, regardless of `neetcode_in_main`.
- anything else → 400.

Each deck has its own daily new-problem limit (`new_per_day` for `main`, `neetcode_new_per_day`
for `neetcode`) and its own "introduced today" count, so introducing a new problem in one deck
never eats into the other deck's limit. In a combined queue (`main` with the toggle on, or
`all`), due problems from both decks are merged and sorted together (weakest recall first); new
problems are the main deck's new problems (up to its limit) followed by the NeetCode deck's
(up to its limit).

## Endpoints

| Method & path | Body / query | Returns |
|---|---|---|
| `GET /api/summary?deck=` | optional deck scope | `{now, today (study date), day_start, day_end, counts:{total, active, suspended, new, due, scheduled, reviewed_today} (scoped to deck), streak_days, recall_rate_30d (0-1 or null), reviews_30d, forecast:[{date:"YYYY-MM-DD", count}] ×14 (index 0 = today incl. overdue), settings, deck_counts:{main:{total,due,new}, neetcode:{total,due,new}}}`. `deck_counts` always covers every active (non-suspended) problem in both decks, regardless of `deck` and the `neetcode_in_main` toggle – it's what the nav badges are built from. |
| `GET /api/queue?tag=&deck=` | optional tag filter, optional deck scope | `{due:[problem…] (weakest recall first, across every included deck), new:[problem…] (main deck's new problems up to its limit, then NeetCode's), new_waiting, new_left_today, new_introduced_today}` (the three `new_*` counts are sums over every included deck) |
| `GET /api/tags?deck=` | optional deck scope | `{tags:[{tag, count}]}` |
| `GET /api/neetcode` | – | NeetCode 150 tracker, see below |
| `POST /api/neetcode/adopt` | `{}` or `{problem_ids:[...]}` | move matching main-deck problems into the NeetCode deck, see below |
| `GET /api/problems?q=&tag=&status=&deck=` | filters optional; `q` searches title, insight, source, prompt, notes and tags; `status` ∈ new, due, scheduled, suspended (anything else → 400); `deck` scopes as above | `{problems:[problem…]}` sorted by due |
| `POST /api/problems` | problem fields (`title` required) + optional `deck` (`main` or `neetcode`; default `main`) + optional `first_rating` (1-4 or null) + optional `duration_ms` | 201, full problem (with history/preview). `first_rating` = "I just solved it, here's how it went"; null = add to the new queue |
| `GET /api/problems/:id` | – | full problem |
| `PATCH /api/problems/:id` | any subset of: title, url, source, difficulty, tags, prompt, insight, notes, solution, language, suspended, deck | full problem. Changing `deck` never touches scheduling |
| `DELETE /api/problems/:id` | `{}` | `{deleted: id}` |
| `POST /api/problems/:id/review` | `{rating: 1-4, duration_ms?: int}` | full problem. Works any time (early reviews are fine – FSRS accounts for elapsed time) |
| `POST /api/problems/:id/undo` | `{}` or `{review_id}` | full problem (last review removed, schedule restored). 400 if no reviews, or if `review_id` is given and is no longer the latest review (already undone / rated again since) |
| `GET /api/settings` | – | `{settings, interval_preview}` |
| `PATCH /api/settings` | any of `desired_retention` (0.70-0.99), `maximum_interval` (days, 1-36500), `again_next_day` (bool), `new_per_day` (0-100), `day_starts_at` (hour 0-23), `fsrs_parameters` (null = FSRS-6 defaults, or 21 numbers within FSRS bounds; normally written by `optimize.py`), `neetcode_in_main` (bool, default false – also show the NeetCode deck on the main Today page and Library), `neetcode_new_per_day` (0-100, default 3), `allow_code_run` (bool, default true – whether the Attempt editor's Run button may execute code, see below), `claude_mode` (`"off"`\|`"api"`\|`"cli"`, default `"off"` – see **Ask Claude** below), `claude_model` (`"haiku"`\|`"sonnet"`\|`"opus"`, default `"sonnet"`) | `{settings, interval_preview}`; changing anything except `new_per_day`, `neetcode_in_main`, `neetcode_new_per_day`, `allow_code_run`, `claude_mode` or `claude_model` replays every problem's history under the new settings, in the same transaction |
| `GET /api/settings/preview?desired_retention=&maximum_interval=` | unsaved values | `{interval_preview}` |
| `GET /api/export` | – | full backup JSON (served as a download) |
| `POST /api/import` | a backup JSON object | `{added, skipped}` (merge; problems already present by uid are skipped; each imported problem's `deck` is kept, defaulting to `main` when absent; a backup's `drafts` are imported too, and older backups made before drafts existed import fine without one) |
| `GET /api/problems/:id/draft` | – | `{code, language, updated_at}` – your saved attempt for this problem (the Attempt editor's autosave). `{code: "", language: "python", updated_at: null}` when nothing is saved yet |
| `PUT /api/problems/:id/draft` | `{code: string (max 100000 chars), language?: string}` | the saved draft, same shape as the GET. `language` defaults to `"python"` when omitted |
| `POST /api/run` | `{code: string (max 100000 chars), stdin?: string}` | `{stdout, stderr, exit_code, timed_out, duration_ms, truncated}` – runs `code` as a real local Python process, see below. 403 when `allow_code_run` is off; 409 when another run is already in progress (only one run at a time) |
| `GET /api/claude/status` | – | `{mode, model, api_key:{set, hint, source}, cli:{found, path}}` – see **Ask Claude** below. Fast; never runs the CLI, only checks it's on PATH |
| `PUT /api/claude/key` | `{api_key: string}` (20-300 chars, no whitespace, must start with `sk-ant-`) | `{ok: true}`. The key is written to `data/secrets.json` (not the database), never returned by any endpoint |
| `DELETE /api/claude/key` | `{}` | `{ok: true}` |
| `POST /api/claude/test` | `{}` | `{ok: true, text, via: "api"\|"cli"}` – makes one tiny real request in the current mode. 409 if `claude_mode` is `"off"`; 502 for upstream failures |
| `POST /api/claude/help` | `{problem_id, code: string (max 100000 chars), run?: {stdout, stderr, exit_code, timed_out}, mode: "hint"\|"debug"\|"explain"\|"review", question?: string (max 4000 chars), history?: [{role: "user"\|"assistant", content: string}] (max 12 entries, each max 20000 chars)}` | `{text, via: "api"\|"cli", model}` – see **Ask Claude** below. 400 on a bad body, 404 if `problem_id` doesn't exist, 409 if `claude_mode` is `"off"`, 502 for upstream failures |

`interval_preview` = `{good_every_time:[days×7], hard_first_then_good:[…], hard_every_time:[…]}` –
the gap before each successive review if you keep rating that way.

Ratings: 1 = Again, 2 = Hard, 3 = Good, 4 = Easy. Numbers are validated strictly: booleans,
fractions, `Infinity`/`NaN` and out-of-range values are rejected with 400. `duration_ms` is
clamped to 0–24 h. On `POST /api/problems`, `first_rating` of `null`, `""` or `0` means "not solved yet".

`POST /api/import` is all-or-nothing for problems (a malformed problem entry → 400 and nothing is
imported); malformed or orphaned review-history entries are skipped. Schedules are kept as-is
when the backup's scheduling settings match this computer's, and recomputed otherwise.

## NeetCode 150 tracker

`GET /api/neetcode` derives progress through the [NeetCode 150](https://neetcode.io/practice)
list from your **NeetCode deck** (`deck: "neetcode"` problems), so there's one source of truth
(no separate checkbox list). A NeetCode-deck problem is matched to a NeetCode problem by its
LeetCode URL slug, falling back to an exact (case-insensitive) title match. A NeetCode problem's
tracker `status` is:

- `not_started` – no matching library problem
- `added` – in the library, never reviewed (reps == 0)
- `solved` – reps >= 1
- `mastered` – reps >= 1, stability >= 21 days, and not suspended

```jsonc
{
  "name": "NeetCode 150", "source": "https://github.com/neetcode-gh/leetcode",
  "categories": [ { "name": "Arrays & Hashing", "total": 9, "solved": 3, "mastered": 1, "added": 1 }, … ],
  "totals": {
    "total": 150, "solved": 12, "mastered": 3, "added": 2, "not_started": 136,
    "by_difficulty": { "Easy": { "total": 45, "solved": 8 }, "Medium": { … }, "Hard": { … } },
    "blind75": { "total": 75, "solved": 10 }
  },
  "problems": [
    {
      "id": 1, "title": "Contains Duplicate", "slug": "contains-duplicate", "leetcode_number": 217,
      "category": "Arrays & Hashing", "difficulty": "Easy", "blind75": true,
      "leetcode_url": "…", "video_url": "…", "solution_url": "…",
      "status": "solved",
      // null when status is "not_started"; otherwise the matched library problem's key fields
      "problem": { "id": 7, "status": "due", "due": "ISO", "due_date": "YYYY-MM-DD", "due_in_days": 2,
                   "retrievability": 0.81, "stability": 9.4, "reps": 2, "lapses": 0, "overdue_days": 0,
                   "last_review": "ISO", "suspended": false }
    }
  ],
  // main-deck problems that look like a NeetCode 150 problem with no NeetCode-deck match yet
  // (same slug/title matching rules); at most one per NeetCode problem (most reps, then lowest id)
  "adoptable": [
    { "problem_id": 12, "title": "Two Sum", "nc_id": 3, "nc_title": "Two Sum", "category": "Arrays & Hashing" }
  ]
}
```

### `POST /api/neetcode/adopt`

Moves main-deck problems from the current `adoptable` list into the NeetCode deck: sets
`deck: "neetcode"` and adds the tags `neetcode-150` and the problem's category (slugged, e.g.
`arrays-hashing`) if not already present, subject to the usual 20-tag cap. Scheduling and review
history are never touched – only `deck` and `tags` change.

- Body `{}` (or omitted `problem_ids`) adopts every problem currently in `adoptable`.
- Body `{"problem_ids": [12, 34]}` adopts only those problem ids; each one must be in the
  current `adoptable` list, or the whole call is rejected with 400 (nothing is changed).
- Returns `{"moved": 2, "tracker": { ...a fresh GET /api/neetcode payload... }}`.

## `POST /api/run` – the local code runner

Runs `code` as `solution.py` in a fresh temporary directory, using the same Python
interpreter that's running the server (`sys.executable`), and returns what happened –
it never raises for a failing/erroring run, only for something going wrong on this
end (bad request, disabled, already running).

```jsonc
// request
{ "code": "print('hi')\nx = int(input())\nprint(x * 2)", "stdin": "21\n" }
// response
{ "stdout": "hi\n42\n", "stderr": "", "exit_code": 0,
  "timed_out": false, "duration_ms": 38, "truncated": false }
```

- `code` runs with a small **prelude** already imported into its globals (not
  prepended to your text, so your own line numbers in a traceback are exact – see
  README for the full list of prelude helpers: `List`/`Dict`/etc. from `typing`,
  `collections`, `heapq`, `bisect`, `math`, `itertools`, `functools`, `string`, `re`,
  `cache`/`lru_cache`, and `ListNode`/`TreeNode`/`build_list`/`list_values`/
  `build_tree`/`tree_values`).
- Runs for up to 10 seconds, then the whole process (and any children it spawned)
  is killed; `timed_out: true` and `exit_code: null` in that case.
- `stdout`/`stderr` are each capped at 64 KB; `truncated: true` if either was cut off.
- Only one run executes at a time, server-wide; a second `POST /api/run` while one is
  in flight gets `409 Conflict` immediately (it does not queue).
- Refused with `403` when the `allow_code_run` setting is off.
- Like every other write endpoint, this needs `Content-Type: application/json` and a
  same-origin (or absent) `Origin` header – see **Security notes** in README.

## Ask Claude (`/api/claude/*`)

Optional debugging help in the Attempt editor ("Ask Claude"), off by default (`claude_mode: "off"`).
Two ways to connect, chosen in Settings → Claude help:

- **`"api"`** – the server calls the Anthropic Messages API directly with a Console API key you
  paste in. Billed per use to your own Anthropic Console account, separate from a Claude Pro/Max
  plan. The key is stored in `data/secrets.json` (not the database, so it's never swept up by
  export/backup), never returned by any endpoint, and never logged.
- **`"cli"`** – the server runs the official, unmodified `claude` command-line tool, which you
  install and sign into yourself (`npm install -g @anthropic-ai/claude-code`, then `claude` once in
  a terminal). The app only ever invokes the binary as a subprocess with a fixed set of flags and
  the conversation on stdin – it never reads or stores the CLI's own credentials, and never asks
  you for a Claude.ai password, cookie or session token (Anthropic's policies for third-party apps
  don't allow that).

`POST /api/claude/help` builds a prompt from the problem's title/difficulty/URL/prompt text (never
your saved insight/notes/solution – those are your own spoilers), your current code, and the
latest run's output, plus instructions for the requested `mode`:

- `hint` – the smallest useful nudge; never the solution.
- `debug` – finds the bug(s), explains why, points at the line(s), and shows a minimal fix for just
  those lines.
- `explain` – explains what the code does and its time/space complexity; no rewrite.
- `review` – an interview-style code review (correctness, edge cases, complexity, readability);
  short snippets are fine here.

`history` carries the panel's prior turns so follow-up questions have context (each request is a
fresh, stateless call to Claude either way – there's no server-side conversation state). Timeouts:
90s for API mode, 180s for CLI mode (`claude` cold starts are slower). `GET /api/claude/status`
never runs the CLI itself, only checks whether it's on `PATH`.
