# DSA Review

A small app that runs on your own computer and brings coding-interview problems back for
practice at the right time, so you remember the *patterns* when the interview comes.

You solve a problem, save it with how it went, and the app decides when you should
recode it again: soon if it was hard, later once you know it well.

---

## Quick start (Windows)

1. You need **Python 3.10 or newer**. Check by opening *Command Prompt* and typing
   `py --version`. If that fails, install Python from <https://www.python.org/downloads/>
   and tick **"Add python.exe to PATH"** in the installer.
2. Double-click **`run.bat`** in this folder.
   - The first time, it sets up a private Python environment (`.venv`) and installs one
     package (`fsrs`). This takes about a minute and needs internet.
   - After that, it starts instantly.
3. Your browser opens at `http://127.0.0.1:8765`. Keep the black terminal window open while you
   study; close it (or press `Ctrl+C` in it, then `Y` if it asks "Terminate batch job?") when
   you're done.

Mac/Linux: run `./run.sh` instead.

> **What's a virtual environment (`.venv`)?** It's a private copy of Python just for this
> app, so the packages it installs don't mix with anything else on your computer. `run.bat`
> creates and uses it for you. If something ever gets into a weird state, delete the `.venv`
> folder and run `run.bat` again. Your data isn't stored there.

## Daily workflow

1. **Solve a new problem** anywhere (LeetCode, *Cracking the Coding Interview*, etc.).
2. **Add problem** → fill in the title, link, tags (the pattern, e.g. `two-pointers`), and most
   importantly the **key insight**: one line with the idea that cracks it. Then pick how it went.
   You can also add problems you *haven't* solved yet; they go into a "new" queue that feeds
   you a few per day (3 by default).
3. **Today** → work through what's due. For each problem:
   - Press **Start** (or `Space`) and **recode it from a blank file** in your editor or on
     LeetCode. Don't look at your notes.
   - When you're done, press **Show my notes** (`N`) to compare.
   - Rate yourself honestly (`1`–`4`):

| Button | Use it when… | What happens |
|---|---|---|
| **Again** | You couldn't solve it without the solution | Comes back tomorrow |
| **Hard** | Solved, but needed a hint, went well over time, or had real bugs | Short gap |
| **Good** | Solved it on your own, about on time | Normal gap (grows each time) |
| **Easy** | Fast and confident, interview-ready | Long gap |

Each button shows how long the next gap will be before you press it (roughly: the app adds a
little random spread, so problems you solve on the same day don't all come back on the same
day). Made a mistake? Hit **Undo** on the pop-up.

Target times shown by the timer: Easy 15 min, Medium 25 min, Hard 40 min (30 min if you didn't
set a difficulty).

## Attempt editor & running code

Every problem page has an **Attempt** card with a real code editor (syntax highlighting,
auto-indent, bracket matching, undo, search – it's [CodeMirror](https://codemirror.net/), see
`tools/codemirror/`). It autosaves what you type (about a second after you stop, and again if
you leave the page), so you can pick a problem back up later exactly where you left it. If
there's nothing saved yet, it starts from a small starter template.

- **Run** (or `Ctrl`/`Cmd`+`Enter`) executes your code as a real Python process **on your own
  computer** – see **Security notes** below.
- **Input (stdin)** opens a small box for standard input, if your code calls `input()`.
- **Reset to template** clears the editor back to the starter template (asks first).
- **Copy** copies the code to your clipboard.
- **Save as my solution** copies the editor's contents into this problem's saved *Solution*
  field (asks first if one is already saved).

Your code runs with a few LeetCode-style helpers already available, so you don't have to
retype them every time:

- Everything from `typing` (`List`, `Dict`, `Optional`, …), plus `collections`, `heapq`,
  `bisect`, `math`, `itertools`, `functools`, `string`, `re`, and `cache`/`lru_cache`.
- `ListNode` / `TreeNode` – the usual LeetCode linked-list and binary-tree node classes.
- `build_list(values)` / `list_values(head)` – convert between a plain list and a `ListNode`
  chain.
- `build_tree(values)` / `tree_values(root)` – convert between LeetCode's level-order list
  (with `None` gaps, e.g. `[3, 9, 20, None, None, 15, 7]`) and a `TreeNode` tree.

A run gets 10 seconds; if it's still going, the whole process is stopped and you'll see
"Timed out". Output is capped at 64 KB per stream. Only one run happens at a time across the
whole app.

The **Today** review card also has a collapsible **Code it here** section, so you can recode a
problem from scratch while reviewing it without leaving the card. It always starts blank (never
your saved draft, which would spoil the recode), but as soon as you start typing it autosaves to
that problem's draft – replacing whatever attempt was saved there before. That's the same draft
the problem page's Attempt editor reads from, so it always reflects your latest attempt.

You can turn code running off entirely (Settings → Code runner → *Allow running code*); you can
still write and save attempts, just not execute them.

## Claude help

The Attempt editor's toolbar has an **Ask Claude** button that opens a panel below the output –
pick Hint, Debug, Explain or Review, optionally ask something specific, and get a reply rendered
as Markdown. It's off by default; turn it on in **Settings → Claude help**, in one of two ways:

- **API key** – paste in an Anthropic Console API key (get one at
  [platform.claude.com](https://platform.claude.com)). Each request is billed per use to that
  Console account, separately from a Claude Pro/Max subscription. The key is saved to
  `data/secrets.json` on your computer (not the app's database, so it's never swept up by
  Export/Import backups), and never appears in any API response.
- **Claude Code CLI** – if you already have the [`claude` CLI](https://claude.com/product/claude-code)
  installed and signed in (`npm install -g @anthropic-ai/claude-code`, then run `claude` once in a
  terminal to sign in), turn this on instead: the app runs that CLI on your computer, using
  whatever Claude plan you're signed into. **The app never asks for, reads or stores your Claude
  login** – it only invokes the `claude` command you've already authenticated yourself. (This
  isn't a shortcut: Anthropic's policies don't allow third-party apps like this one to collect or
  proxy Claude.ai credentials, so "Sign in with Claude" isn't offered here.)

Either way, each request sends Claude the problem's title/difficulty/URL/prompt text, your current
code, and your latest run's output – never your own saved *Key insight*, *Notes* or *Solution*
(those are your spoilers, kept local). Pick a model (Haiku 4.5, Sonnet 5, or Opus 5.5) in Settings;
Settings also has a **Test connection** button to check everything's wired up before you rely on
it mid-review.

## NeetCode 150 tracker

The **NeetCode 150** page lists all 150 problems in roadmap order, grouped by pattern, with
links to LeetCode, NeetCode's video explanation and solution, plus your progress overall, by
difficulty, by pattern and for the Blind 75 subset.

Progress comes from your library, so there's nothing extra to tick off. A NeetCode problem is
matched to a library problem by its LeetCode link (or, failing that, its exact title):

- **Not started**: not in your library.
- **Added**: saved but not solved yet (in your new queue).
- **Solved**: saved with a rating at least once.
- **Mastered**: solved, and its memory strength is 21+ days.

Press **Add** on a row to open *Add problem* with the title, link, difficulty and tags
(`<pattern>`, `neetcode-150`, `blind-75`) already filled in. Solve it, write your key insight,
pick how it went, and save.

### The NeetCode deck and its own review session

NeetCode 150 problems live in a **separate deck** from your main library, with its own
independent review schedule, its own daily "new problems" limit, and its own review session at
**NeetCode → Start review** (or `#/neetcode/review`). Problems you add from the NeetCode 150
page go straight into this deck; anything you add from **Add problem** with **Deck: Main
library** stays out of it (and out of the NeetCode 150 tracker, which only counts NeetCode-deck
problems as progress).

- **Moving problems you already added.** If you'd already saved a NeetCode 150 problem to your
  main library before this existed, the NeetCode page shows a banner ("N problems in your main
  library are NeetCode 150 problems") with a **Move to NeetCode** button. It moves them into the
  NeetCode deck and tags them, without touching their schedule or review history.
- **Showing NeetCode problems on the main Today page too.** By default the two decks are
  entirely separate — the main Today page and Library only show the main deck. If you'd rather
  see everything in one place, turn on **Settings → NeetCode deck → "Show NeetCode problems on
  the main Today page and Library"**. With it on, due and new NeetCode problems also show up in
  your regular Today session and Library (in addition to still having their own
  `#/neetcode/review` session); the Library also has a Main/NeetCode/Both filter regardless of
  this setting.
- Every problem's edit form and detail page show which deck it's in, and let you move it between
  decks directly (moving a problem never changes its schedule).

## How the scheduling works

The app uses **FSRS-6** (Free Spaced Repetition Scheduler), the algorithm behind Anki's modern
scheduler and the newest production version as of 2026. It improves on the classic
Ebbinghaus forgetting curve in two ways:

- **A curve per problem.** Each problem gets a *stability* (how many days until your chance of
  solving it drops to 90%) and a personal *difficulty* (1–10). Every review updates both.
- **A better-fitting curve.** It uses a power-law forgetting curve,
  `R(t) = (1 + f·t/S)^(−decay)`, which matches real review data better than a plain exponential.

A problem comes back when your predicted chance of solving it drops to your **target recall**
(90% by default). Rating *Good* every time with the defaults gives gaps of about
**2 → 11 → 46 → 60 days** (60 is the default cap). Rating *Hard* keeps the gaps shorter.

Changes made for coding problems (they differ from Anki's flashcard defaults):

- No same-day repeats. The shortest gap is 1 day, because recoding a problem 10 minutes later
  proves nothing.
- **Again** always brings the problem back the next day.
- Time is counted in **study days**, which start at 4am, like Anki. A problem due "tomorrow"
  is ready from 4am, and practicing it an hour earlier than you did the day before still
  counts as a full day. A late-night session at 1am counts toward the previous day.
- Gaps are capped at **60 days**, so nothing disappears in the middle of your prep.
- The due list shows the problems you're most likely to have forgotten first, with patterns
  mixed together, just like a real interview.

You can change all of these on the **Settings** page, which shows a live preview of the gaps.
Raise the target recall (e.g. 93–95%) if the gaps feel too long for you, or as interviews
get close.

### Optional: personalize the scheduler

FSRS starts with default parameters learned from millions of flashcard reviews. After roughly
**500 spaced reviews** of your own, you can fit them to how *you* remember problems:

```bat
.venv\Scripts\python -m pip install "fsrs[optimizer]==6.3.2"
.venv\Scripts\python optimize.py
```

The install is large because it includes PyTorch. Close the app before running the script. It
shows the old and new gaps and asks before changing anything. You can undo it with
**Settings → Reset to defaults**.

## Your data

- Everything lives in **`data/dsa_review.db`** (a SQLite file) in this folder. Nothing leaves
  your computer.
- Every time the app starts, it saves a copy to `data/backups/` and keeps the last 10.
- **Settings → Export backup** downloads a JSON file you can keep anywhere (e.g. cloud storage).
  **Import backup** merges one back in and skips problems you already have. It brings back
  problems and their review history, not your settings, so re-check the Settings page after
  moving to a new computer (and run `optimize.py` again if you had personalized the scheduler).
- Your Claude API key (if you saved one) lives in `data/secrets.json`, separately from the
  database. It's never included in a backup export, so re-enter it after moving to a new computer.
- **Settings → Start over** deletes every problem, review and saved attempt (type `DELETE` to
  confirm), e.g. to hand the app to someone new. A copy of the database is saved first as
  `data/backups/before-reset-<time>.db`; to undo, stop the app and copy that file over
  `data/dsa_review.db`. Tick "Also reset settings" to restore the default settings too. Your Claude
  API key is kept. (Someone who clones this project from Git starts empty anyway: `data/` is
  never committed.)

## Handy commands

Run these from this folder in Command Prompt or PowerShell:

```bat
.\run.bat --port 9000
.\run.bat --no-browser
.venv\Scripts\python -m unittest discover -s tests
```

The first uses a different port, the second doesn't open a browser tab, and the third runs the
test suite (400+ tests).

## Security notes

The app is a local web server, so it's built to stay local:

- It listens only on `127.0.0.1`, so other devices on your network can't reach it.
- It checks the `Host` header, which blocks DNS-rebinding attacks.
- Changes are only accepted as `application/json` from its own origin, which blocks other
  websites from submitting forms to it (CSRF).
- It sends a strict Content-Security-Policy, and everything you type is displayed as plain
  text, never as HTML.
- Links must be `http(s)`, so a saved link can't run JavaScript.
- Only web files (HTML, CSS, JS, images and the like) inside `app/static` are served, so
  path-traversal requests get a 404.
- **The Attempt editor's Run button executes code on your own computer**, as your own user
  account, with no sandbox – that's the whole point (it needs to import real modules, read
  `stdin`, etc.), but it does mean you should treat it like running any other script: don't
  paste in code you don't trust. It's only reachable from this app's own page (the same
  Origin/Host/Content-Type checks as every other write request protect it too), it stops
  itself after 10 seconds, and you can turn it off entirely in Settings.
- **Ask Claude is off by default** and only ever talks to Anthropic's own API (`api.anthropic.com`)
  or a `claude` binary already on your computer – never any other host, and the page's
  Content-Security-Policy (`connect-src 'self'`) means the *browser* itself can only ever call
  this app's own server, not any external API directly. In API mode, your key lives in
  `data/secrets.json` (`chmod 600` on macOS/Linux), separate from the database, and is never
  included in any response, export or log line. In CLI mode, the app never reads or stores the
  `claude` CLI's own credentials, and never asks you for a Claude.ai password, cookie or session
  token – see **Claude help** above.

## Project layout

```
run.bat / run.sh        launchers (set up .venv on first run)
requirements.txt        fsrs==6.3.2
optimize.py             optional FSRS parameter fitting
app/server.py           HTTP server + JSON API (standard library only)
app/store.py            SQLite storage, queue, stats, backup/import, drafts
app/runner.py           runs Attempt-editor code as a local Python process (POST /api/run)
app/claude_help.py      "Ask Claude" debugging help (API key or Claude Code CLI, see above)
app/scheduling.py       FSRS-6 wrapper with the coding-problem tweaks above
app/neetcode.py         NeetCode 150 tracker (matches the list against your library)
app/neetcode150.json    the NeetCode 150 list (from github.com/neetcode-gh/leetcode)
app/static/             the web page (HTML/CSS/JS, no external dependencies)
app/static/vendor/      the vendored CodeMirror editor bundle (see tools/codemirror/build.md)
tools/codemirror/       source + build script for the vendored editor bundle (Node/npm; only
                        needed to rebuild it, not to run the app)
docs/API.md             API reference
tests/                  unittest suite
data/                   your database, backups, and secrets.json (Claude API key), created on first run
```

## Troubleshooting

- **"Could not find Python 3.10 or newer"**: install Python (see Quick start) and make sure
  "Add python.exe to PATH" was ticked. Then double-click `run.bat` again.
- **The page doesn't open**: look at the terminal window for the address (the port may be
  8766+ if 8765 was busy) and paste it into your browser.
- **Setup failed halfway**: just run `run.bat` again; it notices and redoes the setup. If it
  keeps failing, delete the `.venv` folder and try once more.
- **The folder is inside OneDrive** (often the case for *Documents* and *Desktop*): OneDrive
  can lock the database while syncing it. If you see "database is locked" errors, move the
  folder somewhere OneDrive doesn't sync, e.g. `C:\dsa-review`, and keep your backups in the
  cloud with **Export backup** instead.
- **Windows SmartScreen warns about `run.bat`**: it's a plain text file. Right-click →
  *Edit* to read exactly what it does.
