"""NeetCode 150 tracker: derives progress from the user's existing library.

There is deliberately no separate "done" checkbox anywhere. A NeetCode problem
counts as solved because a library problem was matched to it and has at least
one review - the same review history that drives the spaced-repetition
schedule. See app/neetcode150.json for the roadmap data itself.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from store import Invalid, _as_int, _slug_tag

DATA_PATH = Path(__file__).resolve().parent / "neetcode150.json"

MASTERED_STABILITY_DAYS = 21

_cache: dict | None = None


def get_data() -> dict:
    """Load and cache app/neetcode150.json (read once per process)."""
    global _cache
    if _cache is None:
        with open(DATA_PATH, encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


_LEETCODE_HOSTS = {"leetcode.com", "leetcode.cn"}
_SLUG_RE = re.compile(r"^/problems/([a-z0-9-]+)")


def extract_leetcode_slug(url: str | None) -> str | None:
    """Pull the problem slug out of a LeetCode URL, or None if it isn't one.

    Handles https://leetcode.com/problems/two-sum/ , with or without a
    trailing slash, "www.", a query string or fragment, and a "/description/"
    or "/solutions/..." suffix. leetcode.cn is treated the same way.
    neetcode.io URLs are not LeetCode URLs, so they never match here.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _LEETCODE_HOSTS:
        return None
    m = _SLUG_RE.match(parsed.path.lower())
    return m.group(1) if m else None


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip()).casefold()


def _match_library_problems(library_problems: list[dict], nc_problems: list[dict]) -> dict[int, dict]:
    """Map NeetCode problem id -> the best-matching library problem (if any).

    Matching is by LeetCode slug first, falling back to an exact (trimmed,
    whitespace-collapsed, case-insensitive) title match. Each library problem
    matches at most one NeetCode problem. When several library problems match
    the same NeetCode problem, the one with the most reviews wins (ties broken
    by the lowest library id).
    """
    by_slug = {p["slug"]: p for p in nc_problems}
    by_title = {_norm_title(p["title"]): p for p in nc_problems}

    best: dict[int, dict] = {}
    for lp in library_problems:
        nc = None
        slug = extract_leetcode_slug(lp.get("url"))
        if slug and slug in by_slug:
            nc = by_slug[slug]
        else:
            nc = by_title.get(_norm_title(lp.get("title", "")))
        if nc is None:
            continue
        nid = nc["id"]
        current = best.get(nid)
        # Prefer more reviews, then the lower library id.
        if current is None or (lp["reps"], -lp["id"]) > (current["reps"], -current["id"]):
            best[nid] = lp
    return best


def _status_for(lp: dict | None) -> str:
    if lp is None:
        return "not_started"
    if lp["reps"] < 1:
        return "added"
    mastered = (
        lp.get("stability") is not None
        and lp["stability"] >= MASTERED_STABILITY_DAYS
        and not lp["suspended"]
    )
    return "mastered" if mastered else "solved"


def _problem_summary(lp: dict) -> dict:
    return {
        "id": lp["id"],
        "status": lp["status"],
        "due": lp["due"],
        "due_date": lp["due_date"],
        "due_in_days": lp["due_in_days"],
        "retrievability": lp["retrievability"],
        "stability": lp["stability"],
        "reps": lp["reps"],
        "lapses": lp["lapses"],
        "overdue_days": lp.get("overdue_days"),
        "last_review": lp["last_review"],
        "suspended": lp["suspended"],
    }


def build_tracker(library_problems: list[dict]) -> dict:
    """Build the /api/neetcode payload from the library's current problems.

    ``library_problems`` should be the whole library (every deck): the NeetCode
    deck is the source of truth for progress, and the main deck is scanned for
    problems that could be moved over (``adoptable``).
    """
    data = get_data()
    nc_problems = data["problems"]
    nc_deck = [p for p in library_problems if p.get("deck", "main") == "neetcode"]
    main_deck = [p for p in library_problems if p.get("deck", "main") == "main"]
    matched = _match_library_problems(nc_deck, nc_problems)

    # NeetCode problems with no match yet in the neetcode deck, but that a main-deck
    # problem looks like a match for (same slug/title rules; at most one per NeetCode
    # problem, preferring the main-deck problem with the most reps, then lowest id).
    unmatched_nc = [nc for nc in nc_problems if nc["id"] not in matched]
    adopt_matches = _match_library_problems(main_deck, unmatched_nc)
    adoptable = []
    for nc in unmatched_nc:
        lp = adopt_matches.get(nc["id"])
        if lp is not None:
            adoptable.append({
                "problem_id": lp["id"], "title": lp["title"],
                "nc_id": nc["id"], "nc_title": nc["title"], "category": nc["category"],
            })

    difficulties = ("Easy", "Medium", "Hard")
    by_difficulty = {d: {"total": 0, "solved": 0} for d in difficulties}
    blind75 = {"total": 0, "solved": 0}
    cat_stats = {c: {"total": 0, "solved": 0, "mastered": 0, "added": 0} for c in data["categories"]}
    totals = {"total": len(nc_problems), "solved": 0, "mastered": 0, "added": 0, "not_started": 0}

    problems_out = []
    for nc in nc_problems:
        lp = matched.get(nc["id"])
        tracker_status = _status_for(lp)
        problems_out.append({**nc, "status": tracker_status, "problem": _problem_summary(lp) if lp else None})

        cat = cat_stats[nc["category"]]
        cat["total"] += 1
        by_difficulty[nc["difficulty"]]["total"] += 1
        if nc["blind75"]:
            blind75["total"] += 1

        solved_like = tracker_status in ("solved", "mastered")
        if solved_like:
            cat["solved"] += 1
            totals["solved"] += 1
            by_difficulty[nc["difficulty"]]["solved"] += 1
            if nc["blind75"]:
                blind75["solved"] += 1
        if tracker_status == "mastered":
            cat["mastered"] += 1
            totals["mastered"] += 1
        elif tracker_status == "added":
            cat["added"] += 1
            totals["added"] += 1
        elif tracker_status == "not_started":
            totals["not_started"] += 1

    totals["by_difficulty"] = by_difficulty
    totals["blind75"] = blind75

    return {
        "name": data["name"],
        "source": data["source"],
        "categories": [{"name": c, **cat_stats[c]} for c in data["categories"]],
        "totals": totals,
        "problems": problems_out,
        "adoptable": adoptable,
    }


def adopt(store, problem_ids: list | None) -> dict:
    """Move main-deck problems that match an unmatched NeetCode problem into the
    NeetCode deck, tagging them ``neetcode-150`` and their category slug.

    ``problem_ids`` (optional) limits the move to a subset of the current
    ``adoptable`` list; each id must be in it, or the whole call is rejected.
    Scheduling and review history are untouched — only ``deck`` and ``tags`` change.
    """
    tracker = build_tracker(store.list_problems(scope="all"))
    by_id = {a["problem_id"]: a for a in tracker["adoptable"]}
    if problem_ids is None:
        targets = list(by_id.values())
    else:
        targets = []
        for raw in problem_ids:
            pid = _as_int(raw, "problem_ids", 1, 2**63 - 1)
            if pid not in by_id:
                raise Invalid(f"problem {pid} is not in the current adoptable list")
            if by_id[pid] not in targets:   # ignore repeated ids
                targets.append(by_id[pid])

    moved = 0
    for t in targets:
        problem = store.get_problem(t["problem_id"], with_history=False)
        tags = list(problem["tags"])
        for tag in ("neetcode-150", _slug_tag(t["category"])):
            if tag and tag not in tags:
                tags.append(tag)
        store.update_problem(t["problem_id"], {"deck": "neetcode", "tags": tags})
        moved += 1

    return {"moved": moved, "tracker": build_tracker(store.list_problems(scope="all"))}
