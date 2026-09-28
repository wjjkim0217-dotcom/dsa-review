"""Tests for app/claude_help.py ("Ask Claude" debugging help) and its endpoints.

No test here makes a real network call or needs a real `claude` CLI: API-mode HTTP
is exercised through claude_help._post_messages (mocked), and CLI-mode subprocess
calls run a small fake `claude` script placed first on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import time
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
# Handles both --output-format json (the plain, non-streaming path) and
# --output-format stream-json (the streaming path): it looks at its own argv to tell
# which one was asked for. FAKE_CLAUDE_MODE selects the outcome for either format:
#   ok            success (a Markdown reply with a heading, a list and a code block)
#   timeout       sleeps past any reasonable test timeout
#   not_logged_in a result/output claiming the CLI isn't signed in
#   error         a generic upstream error
#   badjson       prints something that isn't JSON at all
# FAKE_CLAUDE_STREAM_DELAY (seconds, default 0) is slept between each streamed event,
# so a test can observe deltas arriving before the final line is written.
FAKE_CLAUDE_SRC = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time

    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    pid_file = os.environ.get("FAKE_CLAUDE_PID_FILE")
    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

    argv_file = os.environ.get("FAKE_CLAUDE_ARGV_FILE")
    if argv_file:
        with open(argv_file, "w") as f:
            f.write("\\x1f".join(sys.argv[1:]))

    sysprompt_capture = os.environ.get("FAKE_CLAUDE_SYSPROMPT_CAPTURE_FILE")
    if sysprompt_capture and "--system-prompt-file" in sys.argv:
        src = sys.argv[sys.argv.index("--system-prompt-file") + 1]
        with open(src, encoding="utf-8") as f:
            content = f.read()
        with open(sysprompt_capture, "w", encoding="utf-8") as f:
            f.write(content)

    stdin_text = sys.stdin.read()
    stdin_file = os.environ.get("FAKE_CLAUDE_STDIN_FILE")
    if stdin_file:
        with open(stdin_file, "w") as f:
            f.write(stdin_text)

    if mode == "timeout":
        time.sleep(30)
        sys.exit(0)

    delay = float(os.environ.get("FAKE_CLAUDE_STREAM_DELAY", "0"))
    streaming = ("--output-format" in sys.argv and
                sys.argv[sys.argv.index("--output-format") + 1] == "stream-json")

    def emit(obj):
        print(json.dumps(obj))
        sys.stdout.flush()
        if delay:
            time.sleep(delay)

    full_reply = ("## Heading\\n\\nSome *text* with `inline code` and a list:\\n\\n"
                 "- one\\n- two\\n\\n```python\\nprint(\\'hi\\')\\n```\\n")

    if streaming:
        if mode == "not_logged_in":
            emit({"type": "result", "is_error": True, "subtype": "error",
                  "result": "Invalid API key \\u00b7 Please run /login"})
            sys.exit(1)
        if mode == "error":
            emit({"type": "result", "is_error": True, "subtype": "error",
                  "result": "Something went wrong upstream"})
            sys.exit(1)
        if mode == "badjson":
            print("this is not json")
            sys.stdout.flush()
            sys.exit(1)
        # "ok": a couple of text_delta stream_events (with a delay between them, so a
        # test can see them arrive before the final "result" line), then the result.
        for chunk in ("## Heading\\n\\nSome *text* with `inline code` and a list:\\n\\n- one\\n- two\\n\\n",
                     "```python\\nprint(\\'hi\\')\\n```\\n"):
            emit({"type": "stream_event",
                  "event": {"type": "content_block_delta",
                            "delta": {"type": "text_delta", "text": chunk}}})
        emit({"type": "result", "is_error": False, "subtype": "success", "result": full_reply})
        sys.exit(0)

    # non-streaming (--output-format json)
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

    print(json.dumps({"type": "result", "is_error": False, "result": full_reply, "subtype": "success"}))
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
        self.sysprompt_file = self.bin_dir / "sysprompt.txt"
        self.pid_file = self.bin_dir / "pid.txt"
        old_path = os.environ.get("PATH", "")
        patcher = mock.patch.dict(os.environ, {
            "PATH": f"{self.bin_dir}{os.pathsep}{old_path}",
            "FAKE_CLAUDE_ARGV_FILE": str(self.argv_file),
            "FAKE_CLAUDE_STDIN_FILE": str(self.stdin_file),
            "FAKE_CLAUDE_SYSPROMPT_CAPTURE_FILE": str(self.sysprompt_file),
            "FAKE_CLAUDE_PID_FILE": str(self.pid_file),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_mode(self, mode: str):
        os.environ["FAKE_CLAUDE_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)

    def set_stream_delay(self, seconds: float):
        os.environ["FAKE_CLAUDE_STREAM_DELAY"] = str(seconds)
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_STREAM_DELAY", None)

    def claude_path(self) -> str:
        return shutil.which("claude")

    def wait_for_pid_gone(self, pid: int, timeout: float = 3.0) -> bool:
        """Polls whether process `pid` has exited. Returns True once it has."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False  # exists but we can't signal it - treat as "still there"
            time.sleep(0.02)
        return False


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


# ================================================================ prompts/claude-coach.md
class PromptLoaderTest(unittest.TestCase):
    """claude_help.load_prompts() - see prompts/claude-coach.md for the file format."""

    def test_reads_the_real_prompts_file(self):
        system_prompt, mode_instructions = claude_help.load_prompts()
        self.assertIn("technical interviewer", system_prompt)
        self.assertEqual(set(mode_instructions), set(claude_help.MODES))
        for mode in claude_help.MODES:
            self.assertNotEqual(mode_instructions[mode], claude_help.MODE_INSTRUCTIONS[mode])
        self.assertNotIn("<!--", system_prompt)
        self.assertNotIn("-->", system_prompt)
        for text in mode_instructions.values():
            self.assertNotIn("<!--", text)
            self.assertNotIn("-->", text)

    def test_missing_file_falls_back_to_builtins(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.md"
            with mock.patch.object(claude_help, "_PROMPTS_PATH", missing):
                system_prompt, mode_instructions = claude_help.load_prompts()
        self.assertEqual(system_prompt, claude_help.SYSTEM_PROMPT)
        self.assertEqual(mode_instructions, claude_help.MODE_INSTRUCTIONS)

    def test_missing_mode_section_falls_back_for_that_mode_only(self):
        content = (
            "Custom system prompt text.\n\n"
            "## Mode: hint\n\nCustom hint text.\n\n"
            "## Mode: debug\n\nCustom debug text.\n\n"
            "## Mode: review\n\nCustom review text.\n"
            # "explain" deliberately omitted
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-coach.md"
            path.write_text(content, encoding="utf-8")
            with mock.patch.object(claude_help, "_PROMPTS_PATH", path):
                system_prompt, mode_instructions = claude_help.load_prompts()
        self.assertEqual(system_prompt, "Custom system prompt text.")
        self.assertEqual(mode_instructions["hint"], "Custom hint text.")
        self.assertEqual(mode_instructions["debug"], "Custom debug text.")
        self.assertEqual(mode_instructions["review"], "Custom review text.")
        self.assertEqual(mode_instructions["explain"], claude_help.MODE_INSTRUCTIONS["explain"])

    def test_comments_stripped_and_mode_names_case_insensitive(self):
        content = (
            "<!-- a note for the owner, never sent -->\n"
            "Top prompt <!-- inline note --> text.\n\n"
            "## Mode: HINT\n\nHint body <!-- inline --> here.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-coach.md"
            path.write_text(content, encoding="utf-8")
            with mock.patch.object(claude_help, "_PROMPTS_PATH", path):
                system_prompt, mode_instructions = claude_help.load_prompts()
        self.assertEqual(system_prompt, "Top prompt  text.")
        self.assertNotIn("<!--", system_prompt)
        self.assertEqual(mode_instructions["hint"], "Hint body  here.")

    def test_empty_top_part_falls_back_to_builtin_system_prompt(self):
        content = "<!-- only a comment up here -->\n\n## Mode: hint\n\nCustom hint.\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-coach.md"
            path.write_text(content, encoding="utf-8")
            with mock.patch.object(claude_help, "_PROMPTS_PATH", path):
                system_prompt, mode_instructions = claude_help.load_prompts()
        self.assertEqual(system_prompt, claude_help.SYSTEM_PROMPT)
        self.assertEqual(mode_instructions["hint"], "Custom hint.")

    def test_handle_help_uses_loaded_prompts(self):
        # build_user_turn (called from handle_help/stream_help) receives the loaded
        # mode_instructions, not always the built-in MODE_INSTRUCTIONS - see
        # build_user_turn's mode_instructions parameter.
        content = "Custom system prompt.\n\n## Mode: hint\n\nCUSTOM-HINT-MARKER\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-coach.md"
            path.write_text(content, encoding="utf-8")
            with mock.patch.object(claude_help, "_PROMPTS_PATH", path):
                _system_prompt, mode_instructions = claude_help.load_prompts()
                text = claude_help.build_user_turn(
                    {"title": "Two Sum"}, "code", None, "hint", "", True, mode_instructions)
        self.assertIn("CUSTOM-HINT-MARKER", text)


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


# ================================================================ API mode, streaming
class ApiStreamTest(StoreTestCase):
    """Exercises claude_help.stream_help() end to end in API mode, with
    _post_messages_stream mocked - no real network call, just like ApiModeTest mocks
    _post_messages for the non-streaming path."""

    def setUp(self):
        super().setUp()
        claude_help.save_api_key(self.store, "sk-ant-" + "k" * 30)
        self.store.update_settings({"claude_mode": "api"})
        self.pid = self.add(title="Two Sum")["id"]

    def body(self, **overrides):
        b = {"problem_id": self.pid, "code": "print(1)", "mode": "hint"}
        b.update(overrides)
        return b

    def test_deltas_then_done(self):
        sse_lines = [
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello "}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"world"}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
            "",
        ]
        with mock.patch.object(claude_help, "_post_messages_stream", return_value=iter(sse_lines)):
            events = list(claude_help.stream_help(self.store, self.body()))
        self.assertEqual([e["type"] for e in events], ["delta", "delta", "done"])
        self.assertEqual(events[0]["text"], "Hello ")
        self.assertEqual(events[1]["text"], "world")
        self.assertEqual(events[2]["text"], "Hello world")
        self.assertEqual(events[2]["via"], "api")
        self.assertEqual(events[2]["model"], "sonnet")

    def test_sse_error_event(self):
        sse_lines = [
            "event: error",
            'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            "",
        ]
        with mock.patch.object(claude_help, "_post_messages_stream", return_value=iter(sse_lines)):
            events = list(claude_help.stream_help(self.store, self.body()))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("Overloaded", events[-1]["message"])

    def test_401_before_stream_is_a_normal_claude_error(self):
        # A rejected key surfaces while _post_messages_stream is opening the request
        # (getting the response headers), which stream_help() does eagerly, so this is
        # still a synchronous ClaudeError - the same friendly mapping _call_api uses -
        # not a mid-stream {"type": "error"} event. See the module note above
        # stream_help() for why that split matters (the 200 ndjson headers haven't
        # been sent yet at this point).
        err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        err.read = lambda: json.dumps({"error": {"message": "invalid x-api-key"}}).encode()
        with mock.patch.object(claude_help, "_post_messages_stream", side_effect=err):
            with self.assertRaises(claude_help.ClaudeError) as ctx:
                claude_help.stream_help(self.store, self.body())
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("rejected the API key", ctx.exception.message)

    def test_no_key_is_409_before_stream(self):
        claude_help.delete_api_key(self.store)
        with self.assertRaises(claude_help.ClaudeError) as ctx:
            claude_help.stream_help(self.store, self.body())
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
        # CLI SECURITY: the system prompt goes to a file, never on the command line -
        # see the comment above claude_help.CLI_FIXED_ARGS.
        self.assertIn("--system-prompt-file", argv)
        self.assertNotIn("--system-prompt", argv)  # exact flag, not a prefix match
        self.assertNotIn("sys prompt", argv)
        self.assertEqual(self.sysprompt_file.read_text(encoding="utf-8"), "sys prompt")
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


# ================================================================ CLI mode, streaming
class CliStreamTest(FakeCliTestCase):
    def stream(self, transcript="### User\nhello there", system="sys prompt", timeout=10):
        return claude_help._stream_cli(transcript, system, "sonnet", self.claude_path(), timeout=timeout)

    def test_success_streams_deltas_then_done(self):
        self.set_mode("ok")
        events = list(self.stream())
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(e["type"] == "delta" for e in events[:-1]))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["via"], "cli")
        self.assertEqual(events[-1]["model"], "sonnet")
        self.assertIn("## Heading", events[-1]["text"])
        self.assertIn("```python", events[-1]["text"])
        # the deltas concatenate to (a prefix of) the same text the "done" event carries
        streamed = "".join(e["text"] for e in events[:-1])
        self.assertIn(streamed, events[-1]["text"])
        self.assertGreater(len(streamed), 0)

    def test_deltas_arrive_before_done_is_written(self):
        # A real gap between events, so we can tell the generator is truly yielding
        # deltas as they arrive rather than buffering everything until the process exits.
        self.set_mode("ok")
        self.set_stream_delay(0.3)
        gen = self.stream(timeout=15)
        t0 = time.monotonic()
        first = next(gen)
        first_at = time.monotonic() - t0
        self.assertEqual(first["type"], "delta")
        rest = list(gen)
        done_at = time.monotonic() - t0
        self.assertEqual(rest[-1]["type"], "done")
        # the first delta arrived (well) before the two later 0.3s-apart scripted
        # events (a second delta, then the result line) could have already happened.
        self.assertLess(first_at, 0.2)
        self.assertGreaterEqual(done_at - first_at, 0.2)

    def test_argv_has_no_system_prompt_or_transcript_text(self):
        self.set_mode("ok")
        list(self.stream(transcript="### User\nhello there", system="sys prompt"))
        self.assertEqual(self.stdin_file.read_text(), "### User\nhello there")
        argv = self.argv_file.read_text().split("\x1f")
        self.assertIn("--output-format", argv)
        self.assertIn("stream-json", argv)
        self.assertIn("--verbose", argv)
        self.assertIn("--include-partial-messages", argv)
        self.assertIn("--system-prompt-file", argv)
        self.assertNotIn("--system-prompt", argv)
        self.assertNotIn("sys prompt", argv)
        self.assertEqual(self.sysprompt_file.read_text(encoding="utf-8"), "sys prompt")
        self.assertNotIn("hello there", " ".join(argv))

    def test_not_logged_in(self):
        self.set_mode("not_logged_in")
        events = list(self.stream())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("signed in", events[-1]["message"])

    def test_generic_error(self):
        self.set_mode("error")
        events = list(self.stream())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("Something went wrong upstream", events[-1]["message"])

    def test_timeout(self):
        self.set_mode("timeout")
        events = list(self.stream(timeout=1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("timed out", events[0]["message"].lower())

    def test_bad_output_falls_back_to_error(self):
        self.set_mode("badjson")
        events = list(self.stream())
        self.assertEqual(events[-1]["type"], "error")

    def test_client_disconnect_kills_the_process(self):
        # Simulates server.py calling gen.close() after a failed write to the browser
        # (see Handler._claude_help_stream) - the CLI subprocess must be killed right
        # away, not left running to finish on its own.
        self.set_mode("ok")
        self.set_stream_delay(2)  # long enough that the process is still alive at close()
        gen = self.stream(timeout=10)
        next(gen)  # start the process and get past its first delta
        pid = int(self.pid_file.read_text())
        self.assertFalse(self.wait_for_pid_gone(pid, timeout=0))  # sanity: still running
        gen.close()
        self.assertTrue(self.wait_for_pid_gone(pid, timeout=3),
                        "the fake `claude` process was still running after gen.close()")


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


# ================================================================ streaming endpoint
class ClaudeStreamEndpointTest(ServerTestCase):
    """POST /api/claude/help/stream, against a real (in-process) server, with a fake
    `claude` on PATH for CLI mode - same style as CliModeTest/CliStreamTest, but
    exercising the actual HTTP response (headers, ndjson framing, error statuses)."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory(prefix="fake-claude-")
        self.addCleanup(tmp.cleanup)
        self.bin_dir = Path(tmp.name)
        make_fake_claude(self.bin_dir)
        old_path = os.environ.get("PATH", "")
        patcher = mock.patch.dict(os.environ, {"PATH": f"{self.bin_dir}{os.pathsep}{old_path}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_mode(self, mode: str):
        os.environ["FAKE_CLAUDE_MODE"] = mode
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)

    def stream_events(self, resp):
        body = resp.body.decode("utf-8")
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    def test_success_200_ndjson_lines_ending_in_done(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "cli"})
        self.set_mode("ok")
        pid = self.create(title="Two Sum")["id"]
        resp = self.request("POST", "/api/claude/help/stream",
                            {"problem_id": pid, "code": "print(1)", "mode": "hint"})
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "application/x-ndjson; charset=utf-8")
        self.assertIsNone(resp.headers.get("Content-Length"))
        self.assertEqual(resp.headers.get("Connection"), "close")
        self.assertSecurityHeaders(resp)
        events = self.stream_events(resp)
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(all(e["type"] == "delta" for e in events[:-1]))
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn("## Heading", events[-1]["text"])

    def test_mode_off_409_json(self):
        pid = self.create(title="Two Sum")["id"]
        resp = self.request("POST", "/api/claude/help/stream",
                            {"problem_id": pid, "code": "print(1)", "mode": "hint"})
        self.assertError(resp, 409)

    def test_bad_body_400_json(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "cli"})
        pid = self.create(title="Two Sum")["id"]
        resp = self.request("POST", "/api/claude/help/stream",
                            {"problem_id": pid, "code": "print(1)", "mode": "not-a-mode"})
        self.assertError(resp, 400)

    def test_bad_problem_id_404_json(self):
        self.call("PATCH", "/api/settings", {"claude_mode": "cli"})
        resp = self.request("POST", "/api/claude/help/stream",
                            {"problem_id": 999999, "code": "print(1)", "mode": "hint"})
        self.assertError(resp, 404)

    def test_origin_guard_still_applies(self):
        pid = self.create(title="Two Sum")["id"]
        resp = self.request("POST", "/api/claude/help/stream",
                            {"problem_id": pid, "code": "print(1)", "mode": "hint"},
                            headers={"Origin": "http://evil.example"})
        self.assertError(resp, 403)


if __name__ == "__main__":
    unittest.main()


class PromptFileRobustnessTest(unittest.TestCase):
    """prompts/claude-coach.md as a Windows user might save it."""

    def load_from(self, raw: bytes):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "claude-coach.md"
            path.write_bytes(raw)
            with mock.patch.object(claude_help, "_PROMPTS_PATH", path):
                return claude_help.load_prompts()

    def test_windows_line_endings_and_bom(self):
        text = "<!-- note -->\r\nBe an interviewer.\r\n\r\n## Mode: hint\r\nOne nudge only.\r\n"
        system, modes = self.load_from(b"\xef\xbb\xbf" + text.encode("utf-8"))
        self.assertEqual(system, "Be an interviewer.")
        self.assertEqual(modes["hint"], "One nudge only.")
        self.assertNotIn("﻿", system)

    def test_non_utf8_file_does_not_crash(self):
        system, modes = self.load_from("Be brief \u201cplease\u201d.\n## Mode: debug\nFind it.\n".encode("cp1252"))
        self.assertIn("Be brief", system)
        self.assertEqual(modes["debug"], "Find it.")
