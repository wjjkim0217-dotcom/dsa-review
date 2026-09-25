"""Tests for app/runner.py (the local code runner behind POST /api/run)."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import TESTS_DIR  # noqa: E402  (puts app/ on sys.path)

import runner  # noqa: E402


class RunPythonTest(unittest.TestCase):
    def test_stdout_is_captured(self):
        r = runner.run_python("print('hello')\nprint(1 + 1)")
        self.assertEqual(r["stdout"], "hello\n2\n")
        self.assertEqual(r["stderr"], "")
        self.assertEqual(r["exit_code"], 0)
        self.assertFalse(r["timed_out"])
        self.assertFalse(r["truncated"])
        self.assertIsInstance(r["duration_ms"], int)
        self.assertGreaterEqual(r["duration_ms"], 0)

    def test_stdin_is_passed_through(self):
        r = runner.run_python("name = input()\nprint(f'hi {name}')", stdin="Ada\n")
        self.assertEqual(r["stdout"], "hi Ada\n")
        self.assertEqual(r["exit_code"], 0)

    def test_exception_traceback_shows_solution_py_with_correct_line(self):
        code = "a = 1\nb = 2\nraise ValueError('boom')\n"
        r = runner.run_python(code)
        self.assertNotEqual(r["exit_code"], 0)
        self.assertIn('File "solution.py", line 3, in <module>', r["stderr"])
        self.assertIn("raise ValueError('boom')", r["stderr"])
        self.assertIn("ValueError: boom", r["stderr"])

    def test_prelude_typing_and_collections_available(self):
        code = "x: List[int] = [1, 2, 3]\nprint(collections.Counter(x))\nprint(heapq.nsmallest(1, x))"
        r = runner.run_python(code)
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("Counter(", r["stdout"])
        self.assertIn("[1]", r["stdout"])

    def test_prelude_list_helpers_round_trip(self):
        code = "print(list_values(build_list([1, 2, 3, 4])))"
        r = runner.run_python(code)
        self.assertEqual(r["stdout"], "[1, 2, 3, 4]\n")
        self.assertEqual(r["exit_code"], 0)

    def test_prelude_tree_helpers_round_trip(self):
        code = "print(tree_values(build_tree([3, 9, 20, None, None, 15, 7])))"
        r = runner.run_python(code)
        self.assertEqual(r["stdout"], "[3, 9, 20, None, None, 15, 7]\n")
        self.assertEqual(r["exit_code"], 0)

    def test_tree_helper_trims_trailing_nones(self):
        code = "print(tree_values(build_tree([1, 2, None])))"
        r = runner.run_python(code)
        self.assertEqual(r["stdout"], "[1, 2]\n")

    def test_timeout_kills_the_process(self):
        r = runner.run_python("import time\ntime.sleep(5)", timeout=0.5)
        self.assertTrue(r["timed_out"])
        self.assertIsNone(r["exit_code"])
        self.assertIn("Timed out", r["stderr"])
        self.assertLess(r["duration_ms"], 4000)

    def test_output_is_truncated_past_the_cap(self):
        r = runner.run_python("print('x' * 200000)")
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["stdout"]), runner.MAX_OUTPUT)

    def test_concurrent_run_raises_run_in_progress(self):
        started = threading.Event()
        release = threading.Event()

        # Hold the runner's own lock directly (simplest way to simulate "a run is
        # already in progress" deterministically, without racing two real subprocesses).
        def hold_lock():
            runner._run_lock.acquire()
            started.set()
            release.wait(5)
            runner._run_lock.release()

        t = threading.Thread(target=hold_lock)
        t.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaises(runner.RunInProgress):
                runner.run_python("print(1)")
        finally:
            release.set()
            t.join(5)

    def test_syntax_error_reported_without_crashing(self):
        r = runner.run_python("def broken(:\n    pass")
        self.assertNotEqual(r["exit_code"], 0)
        self.assertIn("SyntaxError", r["stderr"])

    def test_lock_is_released_after_a_run_so_a_later_run_still_works(self):
        r1 = runner.run_python("print('one')")
        r2 = runner.run_python("print('two')")
        self.assertEqual(r1["stdout"], "one\n")
        self.assertEqual(r2["stdout"], "two\n")


if __name__ == "__main__":
    unittest.main()
