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

## Handy commands

Run these from this folder in Command Prompt or PowerShell:

```bat
.\run.bat --port 9000
.\run.bat --no-browser
.venv\Scripts\python -m unittest discover -s tests
```

The first uses a different port, the second doesn't open a browser tab, and the third runs the
test suite (330+ tests).

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

## Project layout

```
run.bat / run.sh        launchers (set up .venv on first run)
requirements.txt        fsrs==6.3.2
optimize.py             optional FSRS parameter fitting
app/server.py           HTTP server + JSON API (standard library only)
app/store.py            SQLite storage, queue, stats, backup/import
app/scheduling.py       FSRS-6 wrapper with the coding-problem tweaks above
app/static/             the web page (HTML/CSS/JS, no external dependencies)
docs/API.md             API reference
tests/                  unittest suite
data/                   your database and backups (created on first run)
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
