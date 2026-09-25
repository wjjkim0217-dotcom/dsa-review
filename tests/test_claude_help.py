"""Tests for app/claude_help.py ("Ask Claude" debugging help) and its endpoints.

No test here makes a real network call or needs a real `claude` CLI: API-mode HTTP
is exercised through claude_help._post_messages (mocked), and CLI-mode subprocess
calls run a small fake `claude` script placed first on PATH.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import StoreTestCase  # noqa: E402  (puts app/ on sys.path)

import claude_help  # noqa: E402
import server  # noqa: E402
from store import Invalid, NotFound  # noqa: E402

# Reuse test_server.py's tiny HTTP client so the endpoint tests read the same way.
from test_server import ServerTestCase  # noqa: E402


# ================================================================ fake `claude` CLI
FAKE_CLAUDE_SRC = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time

    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    stdin_text = sys.stdin.read()
    argv_file = os.environ.get("FAKE_CLAUDE_ARGV_FILE")
    stdin_file = os.environ.get("FAKE_CLAUDE_STDIN_FILE")
    if argv_file:
        with open(argv_file, "w") as f:
            f.write("\\x1f".join(sys.argv[1:]))
    if stdin_file:
        with open(stdin_file, "w") as f:
            f.write(stdin_text)

    if mode == "timeout":
        time.sleep(30)
        sys.exit(0)
    if mode == "not_logged_in":
        print(json.dumps({"type": "result", "is_error": True,
                          "result": "Invalid API key \\u00b7 Please run /login", "subtype": "error"}))
        sys.exit(1)
    if mode == "error":
        print(json.dumps({"type": "result", "is_error": True,
                          "result": "Something went wrong upstream", "subtype": "error"}))
        sys.exit(1)
    if mode == "badjson":
        print("this is not json")
        sys.exit(1)

    reply = ("## Heading\\n\\nSome *text* with `inline code` and a list:\\n\\n"
             "- one\\n- two\\n\\n```python\\nprint(\\'hi\\')\\n```\\n")
    print(json.dumps({"type": "result", "is_error": False, "result": reply, "subtype": "success"}))
''')


def make_fake_claude(dirpath: Path) -> Path:
    script = dirpath / ("claude.py" if False else "claude")
    script.write_text(FAKE_CLAUDE_SRC, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


class FakeCliTestCase(unittest.TestCase):
    """Puts a fake `claude` executable first on PATH for the duration of the test."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="fake-claude-")
        self.addCleanup(tmp.cleanup)
        self.bin_dir = Path(tmp.name)
        make_fake_claude(self.bin_dir)
        self.argv_file = self.bin_dir / "argv.txt"
        self.stdin_file = self.bin_dir / "stdin.txt"
        old_path = os.environ.get("PATH", "")
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{self.bin_dir}{os.pathsep}{old_path}",
            "FAKE_CLAUDE_ARGV_FILE": str(self.argv_file),
            "FAKE_CLAUDE_STDIN_FILE": str(self.stdin_file),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_mode(self, mode: str):
        os.environ["FAKE_CLAUDE_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)


# ================================================================ settings
class SettingsTest(StoreTestCase):
    def test_bad_mode_rejected(self):
        with self.assertRaises(Invalid):
            self.store.update_settings({"claude_mode": "sign-in"})

    def test_bad_model_rejected(self):
        with self.assertRaises(Invalid):
            self.store.update_settings({"claude_model": "gpt-5"})

    def test_defaults(self):
        s = self.store.get_settings()
        self.assertEqual(s["claude_mode"], "off")
        self.assertEqual(s["claude_model"], "sonnet")

    def test_changing_claude_settings_does_not_replay_schedule(self):
        pid = self.add(first_rating=3)["id"]
        due_before = self.store.get_problem(pid)["due"]
        self.store.update_settings({"claude_mode": "api", "claude_model": "opus"})
        due_after = self.store.get_problem(pid)["due"]
        self.assertEqual(due_before, due_after)


# ================================================================ secret storage
class SecretsTest(StoreTestCase):
    def test_save_and_status(self):
        claude_help.save_api_key(self.store, "sk-ant-" + "a" * 30)
        key, source = claude_help.get_api_key(self.store)
        self.assertTrue(key.startswith("sk-ant-"))
        self.assertEqual(source, "file")
        st = claude_help.status(self.store)
        self.assertTrue(st["api_key"]["set"])
        self.assertEqual(st["api_key"]["source"], "file")
        self.assertTrue(st["api_key"]["hint"].endswith(key[-4:]))
        self.assertNotIn(key, json.dumps(st))

    def test_secrets_file_written_and_permissions(self):
        claude_help.save_api_key(self.store, "sk-ant-" + "b" * 30)
        path = self.store.db_path.parent / "secrets.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["anthropic_api_key"], "sk-ant-" + "b" * 30)
        if not sys.platform.startswith("win"):
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_delete(self):
        claude_help.save_api_key(self.store, "sk-ant-" + "c" * 30)
        claude_help.delete_api_key(self.store)
        key, source = claude_help.get_api_key(self.store)
        self.assertIsNone(key)
        self.assertIsNone(source)
        # deleting again (no file) must not raise
        claude_help.delete_api_key(self.store)

    def test_validation(self):
        for bad in ("short", "no-sk-ant-prefix-but-long-enough-xxxxx", "sk-ant-has space-in-it-xxxxx",
                   123, None, "sk-ant-" + "x" * 400):
            with self.subTest(bad=bad), self.assertRaises(Invalid):
                claude_help.save_api_key(self.store, bad)

    def test_env_fallback(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-" + "e" * 30}):
            key, source = claude_help.get_api_key(self.store)
            self.assertEqual(source, "env")
        # file, when present, wins over env
        claude_help.save_api_key(self.store, "sk-ant-" + "f" * 30)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-" + "e" * 30}):
            key, source = claude_help.get_api_key(self.store)
            self.assertEqual(source, "file")
            self.assertTrue(key.endswith("f" * 4))


# ================================================================ prompt building
class PromptBuildingTest(unittest.TestCase):
    problem = {
        "title": "Two Sum", "difficulty": "Easy", "url": "https://leetcode.com/problems/two-sum/",
        "prompt": "Given an array of integers, return indices of the two numbers that add to target.",
        "insight": "use a hash map to remember complements",
        "notes": "O(n) time, O(n) space",
        "solution": "def two_sum(nums, target): ...",
    }

    def test_includes_context_on_first_turn(self):
        text = claude_help.build_user_turn(self.problem, "code here", None, "hint", "", include_context=True)
        self.assertIn("Two Sum", text)
        self.assertIn("Easy", text)
        self.assertIn("leetcode.com", text)
        self.assertIn("indices of the two numbers", text)
        self.assertIn("code here", text)

    def test_excludes_context_on_followup(self):
        text = claude_help.build_user_turn(self.problem, "code here", None, "hint", "", include_context=False)
        self.assertNotIn("Two Sum", text)
        self.assertNotIn("indices of the two numbers", text)

    def test_never_includes_insight_notes_solution(self):
        for mode in claude_help.MODES:
            text = claude_help.build_user_turn(self.problem, "some code", None, mode, "", include_context=True)
            self.assertNotIn("use a hash map to remember complements", text)
            self.assertNotIn("O(n) time, O(n) space", text)
            self.assertNotIn("def two_sum(nums, target)", text)

    def test_includes_run_output(self):
        run = {"stdout": "3\n", "stderr": "Traceback...", "exit_code": 1, "timed_out": False}
        text = claude_help.build_user_turn(self.problem, "code", run, "debug", "", include_context=True)
        self.assertIn("3\n", text)
        self.assertIn("Traceback...", text)

    def test_mode_instructions_differ(self):
        texts = {m: claude_help.MODE_INSTRUCTIONS[m] for m in claude_help.MODES}
        self.assertEqual(len(set(texts.values())), len(texts))
        self.assertIn("hint", texts["hint"].lower())
        self.assertIn("solution", texts["debug"].lower())

    def test_question_included(self):
        text = claude_help.build_user_turn(self.problem, "code", None, "review", "why is this slow?",
                                           include_context=False)
        self.assertIn("why is this slow?", text)


class ValidateHelpBodyTest(StoreTestCase):
    def base(self, **overrides):
        body = {"problem_id": 1, "code": "print(1)", "mode": "hint"}
        body.update(overrides)
        return body

    def test_ok(self):
        out = claude_help.validate_help_body(self.store, self.base())
        self.assertEqual(out["mode"], "hint")
        self.assertEqual(out["history"], [])

    def test_bad_mode(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(mode="nope"))

    def test_bad_problem_id(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(problem_id="1"))

    def test_code_too_long(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(code="x" * 100001))

    def test_question_too_long(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(question="x" * 4001))

    def test_history_too_long(self):
        history = [{"role": "user", "content": "hi"}] * 13
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(history=history))

    def test_history_bad_role(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(self.store, self.base(history=[{"role": "system", "content": "x"}]))

    def test_history_content_too_long(self):
        with self.assertRaises(Invalid):
            claude_help.validate_help_body(
                self.store, self.base(history=[{"role": "user", "content": "x" * 20001}]))

    def test_run_caps_text(self):
        out = claude_help.validate_help_body(
            self.store, self.base(run={"stdout": "x" * 9000, "stderr": "", "exit_code": 0, "timed_out": False}))
        self.assertLessEqual(len(out["run"]["stdout"]), 8000 + 30)


# ================================================================ API mode
class ApiModeTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        claude_help.save_api_key(self.store, "sk-ant-" + "k" * 30)

    def test_success_parses_text_blocks(self):
        fake_result = {"content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}]}
        with mock.patch.object(claude_help, "_post_messages", return_value=fake_result) as m:
            text, via, model = claude_help._call_api(self.store, "sys", [{"role": "user", "content": "hi"}], "sonnet")
        self.assertEqual(text, "Hello world")
        self.assertEqual(via, "api")
        m.assert_called_once()
        # the api key never travels as a positional/log-visible argument; it's a header
        called_payload, called_key, _timeout = m.call_args[0]
        self.assertEqual(called_key, "sk-ant-" + "k" * 30)
        self.assertEqual(called_payload["model"], claude_help.API_MODELS["sonnet"])

    def test_401_is_friendly(self):
        err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        err.read = lambda: json.dumps({"error": {"message": "invalid x-api-key"}}).encode()
        with mock.patch.object(claude_help, "_post_messages", side_effect=err):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help._call_api(self.store, "sys", [], "sonnet")
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("rejected the API key", ctx.exception.message)

    def test_429_is_friendly(self):
        err = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        err.read = lambda: b"{}"
        with mock.patch.object(claude_help, "_post_messages", side_effect=err):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help._call_api(self.store, "sys", [], "sonnet")
        self.assertIn("rate-limited", ctx.exception.message)

    def test_network_error(self):
        with mock.patch.object(claude_help, "_post_messages",
                               side_effect=urllib.error.URLError("no route to host")):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help._call_api(self.store, "sys", [], "sonnet")
        self.assertIn("Couldn't reach", ctx.exception.message)

    def test_timeout(self):
        with mock.patch.object(claude_help, "_post_messages", side_effect=TimeoutError()):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help._call_api(self.store, "sys", [], "sonnet")
        self.assertIn("Timed out", ctx.exception.message)

    def test_no_key_is_409(self):
        claude_help.delete_api_key(self.store)
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help._call_api(self.store, "sys", [], "sonnet")
        self.assertEqual(ctx.exception.status, 409)


# ================================================================ CLI mode
class CliModeTest(FakeCliTestCase):
    def test_success_and_stdin_argv(self):
        self.set_mode("ok")
        text, via, model = claude_help._call_cli("### User\nhello there", "sys prompt", "sonnet", timeout=10)
        self.assertEqual(via, "cli")
        self.assertIn("## Heading", text)
        self.assertIn("```python", text)
        self.assertIn("- one", text)
        self.assertIn("`inline code`", text)
        self.assertEqual(self.stdin_file.read_text(), "### User\nhello there")
        argv = self.argv_file.read_text().split("\x1f")
        self.assertIn("--model", argv)
        self.assertIn("sonnet", argv)
        self.assertIn("--tools", argv)
        self.assertIn("--system-prompt", argv)
        self.assertIn("sys prompt", argv)
        # the transcript (user's code/question) never appears in argv
        self.assertNotIn("hello there", " ".join(argv))

    def test_not_logged_in(self):
        self.set_mode("not_logged_in")
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help._call_cli("hi", "sys", "sonnet", timeout=10)
        self.assertIn("signed in", ctx.exception.message)

    def test_generic_error(self):
        self.set_mode("error")
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help._call_cli("hi", "sys", "sonnet", timeout=10)
        self.assertIn("Something went wrong upstream", ctx.exception.message)

    def test_timeout(self):
        self.set_mode("timeout")
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help._call_cli("hi", "sys", "sonnet", timeout=1)
        self.assertIn("timed out", ctx.exception.message.lower())

    def test_bad_json_output(self):
        self.set_mode("badjson")
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help._call_cli("hi", "sys", "sonnet", timeout=10)
        self.assertEqual(ctx.exception.status, 502)

    def test_cli_not_found(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help._call_cli("hi", "sys", "sonnet", timeout=10)
        self.assertIn("wasn't found", ctx.exception.message)


# ================================================================ endpoints
class ClaudeEndpointsTest(ServerTestCase):
    def test_status_shape(self):
        data = self.call("GET", "/api/claude/status")
        self.assertEqual(data["mode"], "off")
        self.assertEqual(data["model"], "sonnet")
        self.assertFalse(data["api_key"]["set"])
        self.assertIsNone(data["api_key"]["hint"])
        self.assertIn("found", data["cli"])

    def test_key_save_and_delete(self):
        r = self.request("PUT", "/api/claude/key", {"api_key": "sk-ant-" + "z" * 30})
        self.assertEqual(r.status, 200)
        st = self.call("GET", "/api/claude/status")
        self.assertTrue(st["api_key"]["set"])
        self.assertTrue(st["api_key"]["hint"].startswith("…"))
        r = self.request("DELETE", "/api/claude/key", {})
        self.assertEqual(r.status, 200)
        st = self.call("GET", "/api/claude/status")
        self.assertFalse(st["api_key"]["set"])

    def test_key_bad_value_400(self):
        r = self.request("PUT", "/api/claude/key", {"api_key": "too-short"})
        self.assertError(r, 400)

    def test_key_never_in_any_response(self):
        secret = "sk-ant-" + "q" * 40
        self.request("PUT", "/api/claude/key", {"api_key": secret})
        for method, path in (("GET", "/api/claude/status"), ("GET", "/api/settings"), ("GET", "/api/export")):
            resp = self.request(method, path)
            self.assertNotIn(secret, resp.body.decode("utf-8"))

    def test_help_mode_off_409(self):
        pid = self.create()["id"]
        r = self.request("POST", "/api/claude/help",
                         {"problem_id": pid, "code": "print(1)", "mode": "hint"})
        self.assertError(r, 409)

    def test_help_bad_problem_id_404(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "api"})
        claude_help.save_api_key(self.store, "sk-ant-" + "m" * 30)
        r = self.request("POST", "/api/claude/help",
                         {"problem_id": 999999, "code": "print(1)", "mode": "hint"})
        self.assertError(r, 404)

    def test_help_bad_body_400(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "api"})
        pid = self.create()["id"]
        r = self.request("POST", "/api/claude/help",
                         {"problem_id": pid, "code": "print(1)", "mode": "not-a-mode"})
        self.assertError(r, 400)

    def test_help_api_mode_end_to_end(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "api"})
        claude_help.save_api_key(self.store, "sk-ant-" + "n" * 30)
        pid = self.create(title="Two Sum")["id"]
        fake_result = {"content": [{"type": "text", "text": "Try a hash map."}]}
        with mock.patch.object(claude_help, "_post_messages", return_value=fake_result):
            data = self.call("POST", "/api/claude/help",
                             {"problem_id": pid, "code": "print(1)", "mode": "hint"})
        self.assertEqual(data["text"], "Try a hash map.")
        self.assertEqual(data["via"], "api")


if __name__ == "__main__":
    unittest.main()
