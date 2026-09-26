"""Runs the code from the in-app editor as a real local Python process.

This intentionally executes whatever the user pastes or types into the
"Attempt" editor - see the "Security notes" section of README.md. It is only
reachable through /api/run, which (like every other write endpoint) checks
Origin/Host and requires application/json, so only this app's own page can
trigger a run.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

WINDOWS = sys.platform == "win32"
MAX_OUTPUT = 64 * 1024          # cap stdout/stderr each, so a runaway print() can't blow up memory/response size
DEFAULT_TIMEOUT = 10.0          # seconds

# LeetCode-style helpers available to every run, without shifting the user's own
# line numbers (see _bootstrap.py below): typing/collections/etc. plus linked-list
# and binary-tree builders, since interview problems constantly need them.
PRELUDE = '''\
"""Helpers auto-loaded before every run (see app/runner.py). Not part of your file."""
from typing import *
import collections, heapq, bisect, math, itertools, functools, string, re
from collections import *
from functools import cache, lru_cache


class ListNode:
    """Singly-linked list node, LeetCode's usual definition."""

    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next

    def __repr__(self):
        return f"ListNode({self.val!r})"


class TreeNode:
    """Binary tree node, LeetCode's usual definition."""

    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right

    def __repr__(self):
        return f"TreeNode({self.val!r})"


def build_list(values):
    """[1, 2, 3] -> ListNode chain 1 -> 2 -> 3."""
    head = None
    tail = None
    for v in values:
        node = ListNode(v)
        if head is None:
            head = tail = node
        else:
            tail.next = node
            tail = node
    return head


def list_values(head):
    """ListNode chain -> plain list, e.g. for printing/comparing."""
    out = []
    seen = set()
    while head is not None:
        if id(head) in seen:
            raise ValueError("list_values: cycle detected")
        seen.add(id(head))
        out.append(head.val)
        head = head.next
    return out


def build_tree(values):
    """Level-order list with None gaps (LeetCode's format) -> TreeNode root.

    Example: build_tree([3, 9, 20, None, None, 15, 7])
    """
    values = list(values)
    if not values or values[0] is None:
        return None
    it = iter(values)
    root = TreeNode(next(it))
    queue = collections.deque([root])
    while queue:
        node = queue.popleft()
        for side in ("left", "right"):
            try:
                v = next(it)
            except StopIteration:
                return root
            if v is None:
                continue
            child = TreeNode(v)
            setattr(node, side, child)
            queue.append(child)
    return root


def tree_values(root):
    """TreeNode root -> level-order list with None gaps, trailing Nones trimmed."""
    if root is None:
        return []
    out = []
    queue = collections.deque([root])
    while queue:
        node = queue.popleft()
        if node is None:
            out.append(None)
            continue
        out.append(node.val)
        queue.append(node.left)
        queue.append(node.right)
    while out and out[-1] is None:
        out.pop()
    return out
'''

# Runs solution.py with the prelude's names already in its globals, via
# runpy.run_path(..., run_name="__main__") so a traceback's frame for the user's
# own code reads "File "solution.py", line <N>" with the exact line the user
# wrote - no shifting, because the prelude is never prepended to their text.
BOOTSTRAP = '''\
import runpy
import sys
import traceback

import os

# -I (isolated mode) deliberately does not add the script's own directory to
# sys.path, so _prelude (which sits right next to this file, in the process's
# working directory) needs a hand.
sys.path.insert(0, os.getcwd())


def main():
    import _prelude
    init_globals = {k: v for k, v in vars(_prelude).items() if not k.startswith("_")}
    try:
        runpy.run_path("solution.py", init_globals=init_globals, run_name="__main__")
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
'''


class RunInProgress(Exception):
    """Raised when a run is already in flight (only one run at a time)."""


_run_lock = threading.Lock()


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the whole process tree, not just the immediate python process."""
    if WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _drain(stream, sink: dict) -> None:
    """Read a pipe to the end, keeping only the first MAX_OUTPUT bytes.

    Runs in its own thread. It keeps reading (and throwing away) past the cap so the
    child never blocks on a full pipe, but memory stays bounded even if the code
    prints in an infinite loop until the timeout.
    """
    kept = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            room = MAX_OUTPUT - len(kept)
            if room > 0:
                kept += chunk[:room]
            if len(chunk) > room:
                truncated = True
    except (OSError, ValueError):
        pass  # pipe closed while killing the process
    sink["data"] = bytes(kept)
    sink["truncated"] = truncated


def _decode(data: bytes) -> str:
    # Decode leniently: a run can print anything, including binary garbage from a bug.
    return data.decode("utf-8", errors="replace")


def run_python(code: str, stdin: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Run `code` as solution.py in a fresh temp directory. One run at a time.

    Raises RunInProgress if another run is already executing.
    """
    if not _run_lock.acquire(blocking=False):
        raise RunInProgress("a run is already in progress")
    try:
        return _run(code, stdin, timeout)
    finally:
        _run_lock.release()


def _run(code: str, stdin: str, timeout: float) -> dict:
    # ignore_cleanup_errors: on Windows a just-killed process can hold files for a moment.
    with tempfile.TemporaryDirectory(prefix="dsa-run-", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "solution.py").write_text(code, encoding="utf-8")
        (tmp_path / "_prelude.py").write_text(PRELUDE, encoding="utf-8")
        (tmp_path / "_bootstrap.py").write_text(BOOTSTRAP, encoding="utf-8")

        env = dict(os.environ)  # keep PATH/SYSTEMROOT/etc. so Python itself works on Windows
        env["PYTHONIOENCODING"] = "utf-8"

        cmd = [sys.executable, "-I", "-X", "utf8", "_bootstrap.py"]
        popen_kwargs = dict(
            cwd=str(tmp_path),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        start = time.monotonic()
        proc = subprocess.Popen(cmd, **popen_kwargs)
        out_sink: dict = {}
        err_sink: dict = {}
        readers = [threading.Thread(target=_drain, args=(proc.stdout, out_sink), daemon=True),
                   threading.Thread(target=_drain, args=(proc.stderr, err_sink), daemon=True)]
        for t in readers:
            t.start()

        def _write_stdin():
            # Writing directly here would block until the child reads it - a pipe only
            # buffers a small amount (often 64KB), so a larger stdin the child never
            # reads (or reads slowly) would hang this call past `timeout`, past
            # proc.wait() below, and past the caller's lock (see run_python). Doing the
            # write on its own thread means the timeout below always fires on schedule
            # regardless of how much of it the child ever reads.
            try:
                proc.stdin.write((stdin or "").encode("utf-8"))
            except OSError:
                pass  # the program exited without reading its input
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        writer = threading.Thread(target=_write_stdin, daemon=True)
        writer.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            proc.wait()
        for t in readers:
            t.join(timeout=5)
        # The writer can still be blocked on a full pipe after a kill; killing the
        # process closes its end, which unblocks (or errors) the write shortly after.
        writer.join(timeout=5)
        # Close explicitly (rather than leaving it to TemporaryDirectory/GC) now that
        # the reader threads are done with them, so nothing warns about an unclosed pipe.
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        duration_ms = int((time.monotonic() - start) * 1000)

        stdout = _decode(out_sink.get("data", b""))
        stderr = _decode(err_sink.get("data", b""))
        out_trunc = out_sink.get("truncated", False)
        err_trunc = err_sink.get("truncated", False)
        if timed_out:
            note = f"\n[Timed out after {timeout:g}s - process was stopped.]\n"
            stderr = stderr + note

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": None if timed_out else proc.returncode,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "truncated": bool(out_trunc or err_trunc),
        }
