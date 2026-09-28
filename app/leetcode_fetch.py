"""Pulls a LeetCode problem's statement into a NeetCode 150 problem's Prompt field.

Manual only: this module is only ever invoked from POST /api/problems/:id/leetcode-statement,
which the owner triggers by pressing "Load from LeetCode" on a single problem - never
automatically, never in bulk. The fetched text is stored only in the local database
(``store.update_problem``'s normal write path); nothing here ever writes to the repo.

Uses LeetCode's own (unofficial, undocumented) GraphQL endpoint - the same one
leetcode.com's own problem page calls - since there is no official public API. This can
break or get rate-limited at any time; see the friendly error messages below and
README.md's note on it. Respect LeetCode's terms: this is for your own practice, not for
scraping problem sets in bulk.

Deliberately meant to run OUTSIDE Handler.lock (see server.py): it does network I/O and
never touches the SQLite database itself.
"""
from __future__ import annotations

import html.parser
import json
import re
import urllib.error
import urllib.request

from store import TEXT_LIMITS

GRAPHQL_URL = "https://leetcode.com/graphql/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DSA-Review"
TIMEOUT = 20.0  # seconds

QUERY = (
    "query questionContent($titleSlug: String!) { "
    "question(titleSlug: $titleSlug) { title isPaidOnly content } }"
)

PREMIUM_MESSAGE = (
    "This is a LeetCode Premium problem, so LeetCode only shares its statement with "
    "Premium accounts. Open it on LeetCode (or watch NeetCode's video) and paste the "
    "statement with Edit instead."
)
BLOCKED_MESSAGE = (
    "LeetCode refused the request (it sometimes blocks automated requests). Try again "
    "later, or paste the statement with Edit."
)


class FetchError(Exception):
    """An error to report to the browser as {"error": message} with `status` (like
    claude_help.ClaudeError - see server.py's _dispatch, which turns this into JSON)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ============================================================== the HTTP seam

def _post_graphql(payload: dict, timeout: float) -> dict:
    """POSTs to LeetCode's GraphQL endpoint and returns the parsed JSON response body.

    The one seam that does the actual HTTP call - isolated so tests can mock it (like
    claude_help._post_messages). Raises urllib.error.HTTPError on a non-2xx response,
    urllib.error.URLError on a network problem, and TimeoutError if it doesn't finish in
    time. urllib honors the system's HTTP(S)_PROXY environment variables by default.
    """
    slug = ((payload.get("variables") or {}).get("titleSlug")) or ""
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Referer": f"https://leetcode.com/problems/{slug}/",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================== fetch_statement

def fetch_statement(slug: str) -> str:
    """Fetches a LeetCode problem's statement by its URL slug (e.g. "two-sum") and
    returns it as plain text. Raises FetchError with a friendly message + HTTP status
    for anything that goes wrong - a problem LeetCode doesn't recognize, a Premium-only
    problem, LeetCode blocking the request, a network problem, or an unexpected shape."""
    payload = {"query": QUERY, "variables": {"titleSlug": slug}}
    try:
        data = _post_graphql(payload, TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise FetchError(502, BLOCKED_MESSAGE)
        raise FetchError(502, f"LeetCode returned an error ({e.code}).")
    except urllib.error.URLError:
        raise FetchError(502, "Couldn't reach LeetCode. Check your internet connection.")
    except TimeoutError:
        raise FetchError(502, "LeetCode took too long to respond.")

    # Shape check first, deliberately distinct from "question" being null: a response
    # missing "data"/"question" entirely is unexpected (502); {"data": {"question":
    # null}} is LeetCode's own, well-formed way of saying the slug doesn't exist (404).
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict) or "question" not in inner:
        raise FetchError(502, "LeetCode sent back something unexpected.")
    question = inner["question"]
    if question is None:
        raise FetchError(404, "LeetCode didn't recognize this problem.")
    if not isinstance(question, dict):
        raise FetchError(502, "LeetCode sent back something unexpected.")

    content = question.get("content")
    if question.get("isPaidOnly") and not (isinstance(content, str) and content.strip()):
        raise FetchError(403, PREMIUM_MESSAGE)
    if not isinstance(content, str) or not content.strip():
        raise FetchError(502, "LeetCode sent back something unexpected.")

    return html_to_text(content)


# ============================================================== HTML -> plain text
#
# A small tree-building HTMLParser (never regex-only parsing of the markup itself, per
# the task's requirement) followed by a recursive walk that turns the tree into plain
# text. Block-level tags (p, div, pre, ul, ol) each produce one "block"; blocks are
# joined with a blank line. Everything else is inline: its text is collected, with
# whitespace collapsed like a browser would (source formatting doesn't produce spurious
# line breaks), except inside <pre>, where whitespace is kept byte-for-byte.

_BLOCK_TAGS = {"p", "div", "pre", "ul", "ol"}
_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "wbr"}
_PASSTHROUGH_INLINE = {"strong", "b", "em", "i", "span", "font", "u", "small", "a", "abbr", "mark"}

_WS_RE = re.compile(r"[ \t\r\n\f\v]+")
_SPACE_AROUND_NL_RE = re.compile(r" *\n *")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_MULTI_NL_RE = re.compile(r"\n{3,}")


class _Node:
    __slots__ = ("tag", "children")

    def __init__(self, tag: str):
        self.tag = tag
        self.children: list = []  # each item is a str or a _Node


class _TreeBuilder(html.parser.HTMLParser):
    """Builds a lightweight DOM out of the HTML: each _Node.children holds a mix of
    plain strings (text) and child _Nodes, in document order. Tolerant of unclosed /
    mismatched tags (LeetCode's stored HTML, or any of it, doesn't have to be perfect) -
    a stray end tag with no open match is simply ignored."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag)
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return
        # No matching open tag - ignore rather than raising on malformed HTML.

    def handle_data(self, data):
        # "&nbsp;" decodes (convert_charrefs=True) to U+00A0; the spec wants it treated
        # as a normal space everywhere, including inside <pre>.
        self._stack[-1].children.append(data.replace("\xa0", " "))


def _norm_text(s: str) -> str:
    return _WS_RE.sub(" ", s)


def _finalize(text: str) -> str:
    text = _SPACE_AROUND_NL_RE.sub("\n", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def _inline_text(children: list, in_pre: bool) -> str:
    out = []
    for child in children:
        if isinstance(child, str):
            out.append(child if in_pre else _norm_text(child))
        else:
            out.append(_inline_node_text(child, in_pre))
    return "".join(out)


def _inline_node_text(node: "_Node", in_pre: bool) -> str:
    tag = node.tag
    if tag == "br":
        return "\n"
    if tag == "img":
        return "[image]"
    if tag == "sup":
        return "^" + _inline_text(node.children, in_pre)
    if tag == "sub":
        return "_" + _inline_text(node.children, in_pre)
    if tag == "code":
        inner = _inline_text(node.children, in_pre)
        return inner if in_pre else f"`{inner}`"
    if tag in _PASSTHROUGH_INLINE:
        return _inline_text(node.children, in_pre)
    if tag in _BLOCK_TAGS:
        # A block tag showed up somewhere we only expected inline content (malformed /
        # unusual markup) - fall back to flattening it inline rather than losing it.
        return _inline_text(node.children, in_pre)
    # Any other/unrecognized tag (e.g. a stray <a>, <u>, ...): keep just its text.
    return _inline_text(node.children, in_pre)


def _has_block_child(node: "_Node") -> bool:
    return any(isinstance(c, _Node) and c.tag in _BLOCK_TAGS for c in node.children)


def _render_pre(node: "_Node") -> str:
    text = _inline_text(node.children, in_pre=True)
    # By HTML convention, the newline(s) right inside the opening/closing <pre> tags are
    # part of the markup's formatting, not the content - trim blank lines off just the
    # two ends, nothing more (the whole point of <pre> is to keep everything else, tabs
    # and internal blank lines included, exactly as it is).
    return text.strip("\n")


def _render_list(node: "_Node", lines: list, depth: int) -> None:
    ordered = node.tag == "ol"
    indent = "  " * depth
    idx = 0
    for child in node.children:
        if not (isinstance(child, _Node) and child.tag == "li"):
            continue
        idx += 1
        prefix = f"{idx}. " if ordered else "- "
        inline_kids = [c for c in child.children if not (isinstance(c, _Node) and c.tag in ("ul", "ol"))]
        nested_lists = [c for c in child.children if isinstance(c, _Node) and c.tag in ("ul", "ol")]
        text = _finalize(_inline_text(inline_kids, False)).replace("\n", " ")
        lines.append(f"{indent}{prefix}{text}")
        for nested in nested_lists:
            _render_list(nested, lines, depth + 1)


def _blocks_from_children(children: list) -> list:
    blocks: list = []
    inline_buf: list = []

    def flush():
        if not inline_buf:
            return
        text = _finalize(_inline_text(inline_buf, False))
        if text:
            blocks.append(text)
        inline_buf.clear()

    for child in children:
        if isinstance(child, str):
            inline_buf.append(child)
            continue
        tag = child.tag
        if tag in ("p", "div"):
            if _has_block_child(child):
                flush()
                blocks.extend(_blocks_from_children(child.children))
            else:
                flush()
                text = _finalize(_inline_text(child.children, False))
                if text:
                    blocks.append(text)
        elif tag == "pre":
            flush()
            block = _render_pre(child)
            if block.strip():
                blocks.append(block)
        elif tag in ("ul", "ol"):
            flush()
            lines: list = []
            _render_list(child, lines, 0)
            if lines:
                blocks.append("\n".join(lines))
        else:
            inline_buf.append(child)
    flush()
    return blocks


def html_to_text(html_src: str) -> str:
    """Converts a LeetCode problem statement's HTML into readable plain text - see the
    module docstring above for the rules. Never raises on malformed HTML."""
    builder = _TreeBuilder()
    try:
        builder.feed(html_src or "")
        builder.close()
    except Exception:
        pass  # best effort with whatever was parsed so far

    blocks = _blocks_from_children(builder.root.children)
    text = "\n\n".join(blocks)
    text = _MULTI_NL_RE.sub("\n\n", text).strip()

    limit = TEXT_LIMITS["prompt"]
    if len(text) > limit:
        suffix = "\n…[truncated]"
        text = text[: max(0, limit - len(suffix))].rstrip() + suffix
    return text
