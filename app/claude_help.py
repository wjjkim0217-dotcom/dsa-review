""""Ask Claude" debugging help for the Attempt editor.

Two ways to connect, chosen in Settings (``claude_mode``):

- ``"api"``: the server calls the Anthropic Messages API directly with a
  Console API key the user pastes in (pay-per-use, billed to their own
  Anthropic Console account).
- ``"cli"``: the server runs the official, unmodified ``claude`` command-line
  tool the user has already installed and signed into with their own Claude
  plan. We only ever invoke the binary - we never read or touch its stored
  credentials, and we never ask the user for a Claude.ai password, cookie or
  session token. (Anthropic's policies for third-party apps forbid asking for
  or storing Claude.ai login credentials - see README.)

Both paths build the same prompt (see PROMPTING below) and return the same
shape: ``{"text": ..., "via": "api" | "cli", "model": "haiku"|"sonnet"|"opus"}``.

Everything in this module is meant to run OUTSIDE ``Handler.lock``: it does
network I/O / spawns a subprocess and never touches the SQLite database.
"""
from __future__ import annotations

import json
import shutil
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import runner  # for WINDOWS and the process-tree kill helper
from store import Invalid

WINDOWS = sys.platform == "win32"

# ============================================================== settings

# Anthropic Console model names for API mode. The CLI instead takes the short
# alias ("haiku"/"sonnet"/"opus") on its own --model flag, so no mapping is
# needed there.
API_MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5-5",
}
CLAUDE_MODES = ("off", "api", "cli")
CLAUDE_MODELS = tuple(API_MODELS)  # ("haiku", "sonnet", "opus")

API_TIMEOUT = 90.0    # seconds, Anthropic Messages API
CLI_TIMEOUT = 180.0   # seconds, `claude` subprocess (cold starts are slower)
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 1500


class ClaudeError(Exception):
    """An error to report to the browser as {"error": message} with `status`."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ============================================================== API key storage
# Deliberately NOT in SQLite (the whole database gets exported/backed up as
# plain JSON) - it lives in its own file, data/secrets.json, next to the
# database. Never included in any API response, export, log line or
# exception message.

def _secrets_path(store) -> Path:
    return store.db_path.parent / "secrets.json"


def _read_secrets(store) -> dict:
    try:
        raw = _secrets_path(store).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def get_api_key(store) -> tuple[str | None, str | None]:
    """Returns (key, source). source is "file", "env", or None if no key is set."""
    key = _read_secrets(store).get("anthropic_api_key")
    if isinstance(key, str) and key:
        return key, "file"
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key, "env"
    return None, None


def save_api_key(store, raw) -> None:
    if not isinstance(raw, str):
        raise Invalid("api_key must be text")
    key = raw.strip()
    if not (20 <= len(key) <= 300):
        raise Invalid("api_key must be between 20 and 300 characters")
    if any(ch.isspace() for ch in key):
        raise Invalid("api_key must not contain whitespace")
    if not key.startswith("sk-ant-"):
        raise Invalid("that doesn't look like an Anthropic API key (it should start with sk-ant-)")
    path = _secrets_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"anthropic_api_key": key}), encoding="utf-8")
    if not WINDOWS:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # best effort - some filesystems (e.g. exFAT) don't support Unix permissions


def delete_api_key(store) -> None:
    try:
        _secrets_path(store).unlink()
    except FileNotFoundError:
        pass


def key_hint(key: str) -> str:
    """A safe-to-display fragment, e.g. '…ab12'. Never the full key."""
    return f"…{key[-4:]}" if len(key) >= 4 else "…"


# ============================================================== status / test


def status(store) -> dict:
    """Fast - never runs the CLI, only checks whether it's on PATH."""
    s = store.get_settings()
    key, source = get_api_key(store)
    cli_path = shutil.which("claude")
    return {
        "mode": s["claude_mode"],
        "model": s["claude_model"],
        "api_key": {"set": bool(key), "hint": key_hint(key) if key else None, "source": source},
        "cli": {"found": bool(cli_path), "path": cli_path},
    }


def test_connection(store) -> dict:
    """POST /api/claude/test: a tiny real request in the current mode."""
    s = store.get_settings()
    mode = s["claude_mode"]
    if mode == "off":
        raise ClaudeError(409, "Claude help is off. Turn it on in Settings → Claude help.")
    text, via, model = _call(store, SYSTEM_PROMPT, [{"role": "user", "content": "Reply with just OK"}],
                             "Reply with just OK", mode, s["claude_model"])
    return {"ok": True, "text": text, "via": via}


# ============================================================== prompting
# All the prompt text lives here, in one place, so it's easy to tweak.

SYSTEM_PROMPT = (
    "You are an interview coach helping someone practice a coding-interview problem "
    "in Python, on their own. Be concise and encouraging. Always answer in Markdown, "
    "and put any code in fenced ```python blocks. Never invent details about the "
    "problem that weren't given to you."
)

MODE_INSTRUCTIONS = {
    "hint": (
        "Mode: HINT. Give the smallest useful nudge - a question to consider or an "
        "observation about their approach. Do NOT write the solution or corrected "
        "code, and do NOT reveal the key trick outright. Keep it to about 120 words "
        "or less."
    ),
    "debug": (
        "Mode: DEBUG. Find the bug(s) causing the failure or wrong output, explain "
        "why it happens, and point to the specific line(s). Show a minimal fix for "
        "just the buggy line(s) - not a full rewrite of their solution."
    ),
    "explain": (
        "Mode: EXPLAIN. Explain what their current code does, step by step, and its "
        "time and space complexity. Do not rewrite or fix it."
    ),
    "review": (
        "Mode: REVIEW. Give an interview-style code review: correctness, edge cases, "
        "time/space complexity, and readability. Suggest improvements briefly; short "
        "illustrative snippets are fine here."
    ),
}
MODES = tuple(MODE_INSTRUCTIONS)


def _cap_text(value, limit: int) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    if len(value) > limit:
        return value[:limit] + "\n...[truncated]"
    return value


def build_user_turn(problem: dict, code: str, run: dict | None, mode: str,
                    question: str, include_context: bool) -> str:
    """The text for one user turn. Deliberately excludes the problem's saved
    insight/notes/solution - those are the user's own spoilers, never sent."""
    parts = []
    if include_context:
        header = [f"Problem: {problem.get('title', '')}"]
        if problem.get("difficulty"):
            header.append(f"Difficulty: {problem['difficulty']}")
        if problem.get("url"):
            header.append(f"URL: {problem['url']}")
        parts.append("\n".join(header))
        prompt_text = (problem.get("prompt") or "").strip()
        if prompt_text:
            parts.append(f"Problem statement:\n{prompt_text}")
    parts.append(MODE_INSTRUCTIONS[mode])
    parts.append(f"Their current code:\n```python\n{code}\n```")
    if run:
        lines = [f"Exit code: {run.get('exit_code')}", f"Timed out: {bool(run.get('timed_out'))}"]
        if run.get("stdout"):
            lines.append(f"stdout:\n{run['stdout']}")
        if run.get("stderr"):
            lines.append(f"stderr:\n{run['stderr']}")
        parts.append("Latest run of that code:\n" + "\n".join(lines))
    else:
        parts.append("They haven't run this code yet.")
    if question:
        parts.append(f"Their follow-up question:\n{question}")
    return "\n\n".join(parts)


def build_transcript(history: list[dict], latest_user_text: str) -> str:
    """Flatten a message list into plain text for the CLI (single-shot, no
    built-in multi-turn conversation object - see _call_cli)."""
    parts = []
    for turn in history:
        role = "User" if turn["role"] == "user" else "Assistant"
        parts.append(f"### {role}\n{turn['content']}")
    parts.append(f"### User\n{latest_user_text}")
    return "\n\n".join(parts)


# ============================================================== request validation

def validate_help_body(store, body: dict) -> dict:
    if not isinstance(body.get("problem_id"), int) or isinstance(body.get("problem_id"), bool):
        raise Invalid("problem_id must be a whole number")
    code = body.get("code")
    if not isinstance(code, str):
        raise Invalid("code must be text")
    if len(code) > 100000:
        raise Invalid("code is too long (max 100000 characters)")

    run = body.get("run")
    run_clean = None
    if run is not None:
        if not isinstance(run, dict):
            raise Invalid("run must be an object")
        run_clean = {
            "stdout": _cap_text(run.get("stdout"), 8000),
            "stderr": _cap_text(run.get("stderr"), 8000),
            "exit_code": run.get("exit_code"),
            "timed_out": bool(run.get("timed_out")),
        }

    mode = body.get("mode")
    if mode not in MODES:
        raise Invalid(f"mode must be one of: {', '.join(MODES)}")

    question = body.get("question") or ""
    if not isinstance(question, str):
        raise Invalid("question must be text")
    question = question.strip()
    if len(question) > 4000:
        raise Invalid("question is too long (max 4000 characters)")

    history = body.get("history") or []
    if not isinstance(history, list):
        raise Invalid("history must be a list")
    if len(history) > 12:
        raise Invalid("history can have at most 12 messages")
    clean_history = []
    for i, turn in enumerate(history):
        if not isinstance(turn, dict):
            raise Invalid(f"history[{i}] must be an object")
        role = turn.get("role")
        if role not in ("user", "assistant"):
            raise Invalid(f"history[{i}].role must be 'user' or 'assistant'")
        content = turn.get("content")
        if not isinstance(content, str) or not content:
            raise Invalid(f"history[{i}].content must be non-empty text")
        if len(content) > 20000:
            raise Invalid(f"history[{i}].content is too long (max 20000 characters)")
        clean_history.append({"role": role, "content": content})

    return {
        "problem_id": body["problem_id"], "code": code, "run": run_clean,
        "mode": mode, "question": question, "history": clean_history,
    }


# ============================================================== POST /api/claude/help

def handle_help(store, body: dict) -> dict:
    data = validate_help_body(store, body)
    settings = store.get_settings()
    mode_setting = settings["claude_mode"]
    if mode_setting == "off":
        raise ClaudeError(409, "Claude help is off. Turn it on in Settings → Claude help.")

    problem = store.get_problem(data["problem_id"], with_history=False)
    include_context = not data["history"]
    user_text = build_user_turn(problem, data["code"], data["run"], data["mode"],
                                data["question"], include_context)
    messages = [*data["history"], {"role": "user", "content": user_text}]
    text, via, model = _call(store, SYSTEM_PROMPT, messages, user_text, mode_setting,
                             settings["claude_model"], history=data["history"])
    return {"text": text, "via": via, "model": model}


def _call(store, system: str, messages: list[dict], latest_user_text: str, mode_setting: str,
         model_alias: str, history: list[dict] | None = None) -> tuple[str, str, str]:
    if mode_setting == "api":
        return _call_api(store, system, messages, model_alias)
    transcript = build_transcript(history or [], latest_user_text)
    return _call_cli(transcript, system, model_alias)


# ============================================================== API mode

def _post_messages(payload: dict, api_key: str, timeout: float) -> dict:
    """The one seam that does the actual HTTP call - isolated so tests can mock it.

    Returns the parsed JSON response body on success. Raises urllib.error.HTTPError
    on a non-2xx response, urllib.error.URLError on a network problem, and
    TimeoutError if the request doesn't complete in time.
    """
    req = urllib.request.Request(
        MESSAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
    )
    # urllib honors the system's HTTP(S)_PROXY environment variables by default.
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_api(store, system: str, messages: list[dict], model_alias: str,
             timeout: float = API_TIMEOUT) -> tuple[str, str, str]:
    api_key, _source = get_api_key(store)
    if not api_key:
        raise ClaudeError(409, "No Anthropic API key saved. Add one in Settings → Claude help.")
    payload = {
        "model": API_MODELS[model_alias],
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
    }
    try:
        result = _post_messages(payload, api_key, timeout)
    except urllib.error.HTTPError as e:
        status_code = e.code
        try:
            err_body = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_body = {}
        detail = ((err_body or {}).get("error") or {}).get("message", "")
        if status_code in (401, 403):
            raise ClaudeError(502, "Anthropic rejected the API key. Check the key saved in "
                                   "Settings → Claude help.")
        if status_code == 429:
            raise ClaudeError(502, "Anthropic rate-limited this request. Wait a moment and try again.")
        if status_code in (503, 529):
            raise ClaudeError(502, "Anthropic's API is temporarily overloaded. Try again shortly.")
        raise ClaudeError(502, f"Anthropic API error ({status_code}). {detail or 'Try again later.'}")
    except urllib.error.URLError:
        raise ClaudeError(502, "Couldn't reach Anthropic's API. Check your internet connection.")
    except TimeoutError:
        raise ClaudeError(502, f"Timed out waiting for Anthropic's API (after {timeout:g}s).")

    text = "".join(block.get("text", "") for block in result.get("content", [])
                   if isinstance(block, dict) and block.get("type") == "text")
    return text, "api", model_alias


# ============================================================== CLI mode
#
# Flags used and why (confirmed against `npx @anthropic-ai/claude-code claude --help`):
#   -p / --print              non-interactive: print the final result and exit.
#   --output-format json      structured output ({"result": ..., "is_error": ...}) -
#                              far easier and safer to parse than the human-readable
#                              text format.
#   --model <alias>           "haiku" | "sonnet" | "opus" - the short alias the CLI
#                              itself documents (resolves to that tier's latest model).
#   --system-prompt <text>    replaces the CLI's own default system prompt outright
#                              (unlike --append-system-prompt, which adds to it), so
#                              the coach prompt above is all the model sees.
#   --tools ""                "" disables every built-in tool (Bash, Edit, Read, ...).
#                              We only want a text reply, never file/shell access.
#   --strict-mcp-config        (no --mcp-config given) refuses to load the user's own
#                              MCP servers for this call, for the same reason.
#   --no-session-persistence   don't write this one-off call to ~/.claude session
#                              history (only valid with --print).
# The conversation text goes on STDIN, never as a command-line argument - the CLI
# resolves its `prompt` positional from stdin when none is given on the command
# line, and this avoids any Windows .cmd argument-quoting injection risk. Every
# argv entry above is a fixed, app-controlled literal; no user text ever reaches argv.
# There is no --max-turns flag in this CLI version; --tools "" already means the
# model cannot call a tool, so there is nothing for it to loop on.

CLI_FIXED_ARGS = [
    "-p", "--output-format", "json",
    "--tools", "",
    "--strict-mcp-config",
    "--no-session-persistence",
]

NOT_LOGGED_IN_MARKERS = (
    "not logged in", "please run", "/login", "claude auth login",
    "log in to claude", "not authenticated", "invalid api key",
)


def _run_cli_process(cmd: list[str], stdin_text: str, cwd: Path, timeout: float):
    env = dict(os.environ)
    popen_kwargs = dict(
        cwd=str(cwd), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if runner.WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(input=stdin_text.encode("utf-8"), timeout=timeout)
        return stdout, stderr, proc.returncode, False
    except subprocess.TimeoutExpired:
        runner._kill_tree(proc)  # same helper /api/run uses to kill the whole process tree
        stdout, stderr = proc.communicate()
        return stdout, stderr, proc.returncode, True


def _call_cli(transcript: str, system: str, model_alias: str,
             timeout: float = CLI_TIMEOUT) -> tuple[str, str, str]:
    claude_path = shutil.which("claude")
    if not claude_path:
        raise ClaudeError(502, "The `claude` command wasn't found on this computer's PATH. Install it "
                               "with `npm install -g @anthropic-ai/claude-code`, then run `claude` once "
                               "in a terminal to sign in.")
    cmd = [claude_path, *CLI_FIXED_ARGS, "--model", model_alias, "--system-prompt", system]
    with tempfile.TemporaryDirectory(prefix="dsa-claude-", ignore_cleanup_errors=True) as tmp:
        try:
            stdout, stderr, returncode, timed_out = _run_cli_process(cmd, transcript, Path(tmp), timeout)
        except OSError as e:
            raise ClaudeError(502, f"Couldn't start the `claude` command ({e}).")

    if timed_out:
        raise ClaudeError(502, f"The `claude` command timed out (after {timeout:g}s).")

    out_text = stdout.decode("utf-8", errors="replace")
    err_text = stderr.decode("utf-8", errors="replace")
    combined_lower = (out_text + "\n" + err_text).lower()

    data = None
    try:
        data = json.loads(out_text)
    except json.JSONDecodeError:
        pass

    if data is not None and isinstance(data, dict) and not data.get("is_error"):
        return data.get("result", ""), "cli", model_alias

    if any(marker in combined_lower for marker in NOT_LOGGED_IN_MARKERS):
        raise ClaudeError(502, "The `claude` CLI isn't signed in. Open a terminal, run `claude`, and "
                               "sign in with your own Claude plan, then try again.")

    if data is not None and isinstance(data, dict):
        message = data.get("result") or data.get("error") or "unknown error"
        raise ClaudeError(502, f"The `claude` CLI reported an error: {_cap_text(str(message), 400)}")

    detail = _cap_text((err_text or out_text).strip(), 400) or f"exit code {returncode}"
    raise ClaudeError(502, f"The `claude` CLI failed unexpectedly: {detail}")
