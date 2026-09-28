"""Tests for app/leetcode_fetch.py ("Load from LeetCode").

No test here makes a real network call, and no test HTML/text is (or resembles) a real
LeetCode problem statement - everything is a made-up, fictional sample that merely
mimics LeetCode's markup structure (see the module docstring in app/leetcode_fetch.py).
`fetch_statement`'s own HTTP call is exercised entirely through the one seam,
`_post_graphql` (mocked), exactly like claude_help._post_messages in test_claude_help.py.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _support import TESTS_DIR  # noqa: E402  (puts app/ on sys.path)

import leetcode_fetch  # noqa: E402

assert TESTS_DIR  # keep the import (for its sys.path side effect) from being "unused"


# ================================================================ html_to_text
class HtmlToTextTest(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(leetcode_fetch.html_to_text(""), "")
        self.assertEqual(leetcode_fetch.html_to_text(None), "")

    def test_paragraphs_become_blocks(self):
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "First paragraph.\n\nSecond paragraph.")

    def test_source_line_wrapping_collapses_to_spaces(self):
        html = "<p>This sentence\n    is wrapped\n    across several source lines.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html),
                         "This sentence is wrapped across several source lines.")

    def test_nbsp_only_paragraphs_are_dropped(self):
        html = "<p>Keep this.</p><p>&nbsp;</p><p>&nbsp;&nbsp;</p><p>And this.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "Keep this.\n\nAnd this.")

    def test_nbsp_becomes_a_normal_space(self):
        html = "<p>a&nbsp;b</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "a b")

    def test_br_becomes_newline(self):
        html = "<p>Line one.<br>Line two.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "Line one.\nLine two.")

    def test_div_also_starts_a_new_block(self):
        html = "<div>First.</div><div>Second.</div>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "First.\n\nSecond.")

    def test_wrapping_div_with_block_children_is_flattened(self):
        html = "<div><p>Inside a wrapper div.</p><p>Still inside it.</p></div>"
        self.assertEqual(leetcode_fetch.html_to_text(html),
                         "Inside a wrapper div.\n\nStill inside it.")

    def test_pre_preserves_internal_whitespace_exactly(self):
        html = "<pre>fictional_input = [3, 5, 9]\n    fictional_target = 8\noutput = [0, 1]</pre>"
        self.assertEqual(leetcode_fetch.html_to_text(html),
                         "fictional_input = [3, 5, 9]\n    fictional_target = 8\noutput = [0, 1]")

    def test_pre_strips_leading_and_trailing_blank_lines_only(self):
        html = "<pre>\n\nline one\n\n    line two\n\n</pre>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "line one\n\n    line two")

    def test_strong_inside_pre_is_flattened_to_plain_text(self):
        html = "<pre><strong>Input:</strong> widgets = [1, 2]\n<strong>Output:</strong> 3</pre>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "Input: widgets = [1, 2]\nOutput: 3")

    def test_code_inside_pre_gets_no_backticks(self):
        html = "<pre>call <code>solve(widgets)</code> to get the answer</pre>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "call solve(widgets) to get the answer")

    def test_code_outside_pre_gets_backticks(self):
        html = "<p>Call <code>solve(widgets)</code> once.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "Call `solve(widgets)` once.")

    def test_unordered_list(self):
        html = "<ul><li>First widget.</li><li>Second widget.</li></ul>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "- First widget.\n- Second widget.")

    def test_ordered_list_numbers_each_item(self):
        html = "<ol><li>Step one.</li><li>Step two.</li><li>Step three.</li></ol>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "1. Step one.\n2. Step two.\n3. Step three.")

    def test_nested_list_indented_two_spaces_and_renumbers(self):
        html = (
            "<ul><li>Outer one"
            "<ul><li>Inner a</li><li>Inner b</li></ul>"
            "</li><li>Outer two</li></ul>"
        )
        self.assertEqual(
            leetcode_fetch.html_to_text(html),
            "- Outer one\n  - Inner a\n  - Inner b\n- Outer two",
        )

    def test_nested_ordered_inside_unordered(self):
        html = "<ul><li>Constraints<ol><li>First rule</li><li>Second rule</li></ol></li></ul>"
        self.assertEqual(
            leetcode_fetch.html_to_text(html),
            "- Constraints\n  1. First rule\n  2. Second rule",
        )

    def test_sup_and_sub(self):
        html = "<p>A bound of 10<sup>4</sup> and an index base<sub>0</sub>.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "A bound of 10^4 and an index base_0.")

    def test_passthrough_inline_tags_keep_only_their_text(self):
        html = "<p><strong>Bold</strong> and <em>italic</em> and <span>spanned</span> and <font>fonted</font>.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "Bold and italic and spanned and fonted.")

    def test_entities_are_unescaped(self):
        html = "<p>1 &lt; 2 &amp;&amp; 3 &gt; 2, and it&#39;s &quot;fine&quot;.</p>"
        self.assertEqual(leetcode_fetch.html_to_text(html), "1 < 2 && 3 > 2, and it's \"fine\".")

    def test_img_becomes_image_placeholder(self):
        html = '<p>See the diagram: <img src="fictional-diagram.png" alt="diagram"></p>'
        self.assertEqual(leetcode_fetch.html_to_text(html), "See the diagram: [image]")

    def test_caps_and_truncates_at_prompt_limit(self):
        html = "<p>" + ("w" * 25000) + "</p>"
        text = leetcode_fetch.html_to_text(html)
        self.assertEqual(len(text), 20000)
        self.assertTrue(text.endswith("…[truncated]"))

    def test_short_text_is_not_truncated(self):
        html = "<p>Short and fictional.</p>"
        text = leetcode_fetch.html_to_text(html)
        self.assertFalse(text.endswith("[truncated]"))

    def test_malformed_html_does_not_raise(self):
        html = "<p>Unclosed paragraph <strong>still bold<p>Next paragraph</p>"
        # Just must not raise; exact text isn't asserted for genuinely broken markup.
        leetcode_fetch.html_to_text(html)

    def test_full_fictional_sample(self):
        """A realistic-shaped (but entirely made up) "Sum Pairs" problem statement,
        combining most of the rules above the way a real LeetCode page's markup does."""
        html = (
            "<p>Given an array of fictional <code>widgets</code> and a fictional "
            "<code>target</code>, return the indices of the two widgets whose weight "
            "adds up to <code>target</code>.</p>"
            "<p>&nbsp;</p>"
            "<p><strong>Example 1:</strong></p>"
            "<pre>\n<strong>Input:</strong> widgets = [3,5,9], target = 8\n"
            "<strong>Output:</strong> [0,1]\n</pre>"
            "<p>&nbsp;</p>"
            "<p><strong>Constraints:</strong></p>"
            "<ul>\n\t<li><code>2 &lt;= widgets.length &lt;= 10<sup>4</sup></code></li>\n"
            "\t<li>Only one valid pair exists.</li>\n</ul>"
        )
        expected = (
            "Given an array of fictional `widgets` and a fictional `target`, return "
            "the indices of the two widgets whose weight adds up to `target`.\n\n"
            "Example 1:\n\nInput: widgets = [3,5,9], target = 8\nOutput: [0,1]\n\n"
            "Constraints:\n\n- `2 <= widgets.length <= 10^4`\n- Only one valid pair exists."
        )
        self.assertEqual(leetcode_fetch.html_to_text(html), expected)


# ================================================================ _post_graphql (the HTTP seam)
class PostGraphqlRequestTest(unittest.TestCase):
    """Confirms the request _post_graphql builds - payload, headers, timeout - without
    hitting the network (urllib.request.urlopen itself is mocked)."""

    def test_request_shape(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({"data": {"question": None}}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return FakeResponse()

        payload = {"query": leetcode_fetch.QUERY, "variables": {"titleSlug": "sum-pairs"}}
        with mock.patch.object(leetcode_fetch.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = leetcode_fetch._post_graphql(payload, 20.0)

        req = captured["req"]
        self.assertEqual(req.full_url, leetcode_fetch.GRAPHQL_URL)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Referer"), "https://leetcode.com/problems/sum-pairs/")
        self.assertIn("DSA-Review", req.get_header("User-agent"))
        sent = json.loads(req.data.decode("utf-8"))
        self.assertEqual(sent, payload)
        self.assertEqual(captured["timeout"], 20.0)
        self.assertEqual(result, {"data": {"question": None}})


# ================================================================ fetch_statement
class FetchStatementTest(unittest.TestCase):
    FICTIONAL_HTML = "<p>A fictional statement about fictional <code>widgets</code>.</p>"
    FICTIONAL_TEXT = "A fictional statement about fictional `widgets`."

    def _mock(self, **kw):
        return mock.patch.object(leetcode_fetch, "_post_graphql", **kw)

    def _question(self, **overrides):
        q = {"title": "Sum Pairs", "isPaidOnly": False, "content": self.FICTIONAL_HTML}
        q.update(overrides)
        return {"data": {"question": q}}

    def test_success_converts_html_to_text(self):
        with self._mock(return_value=self._question()):
            text = leetcode_fetch.fetch_statement("sum-pairs")
        self.assertEqual(text, self.FICTIONAL_TEXT)

    def test_question_null_is_404(self):
        with self._mock(return_value={"data": {"question": None}}):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("does-not-exist")
        self.assertEqual(cm.exception.status, 404)
        self.assertIn("didn't recognize", cm.exception.message)

    def test_premium_with_empty_content_is_403(self):
        with self._mock(return_value=self._question(isPaidOnly=True, content="")):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("premium-problem")
        self.assertEqual(cm.exception.status, 403)
        self.assertIn("Premium", cm.exception.message)

    def test_premium_with_null_content_is_403(self):
        with self._mock(return_value=self._question(isPaidOnly=True, content=None)):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("premium-problem")
        self.assertEqual(cm.exception.status, 403)

    def test_premium_with_content_still_succeeds(self):
        # Some Premium problems' content IS included (e.g. the account making the
        # request has Premium) - isPaidOnly alone shouldn't block it.
        with self._mock(return_value=self._question(isPaidOnly=True, content=self.FICTIONAL_HTML)):
            text = leetcode_fetch.fetch_statement("premium-problem")
        self.assertEqual(text, self.FICTIONAL_TEXT)

    def test_http_error_403_is_blocked_message(self):
        err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with self._mock(side_effect=err):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("blocks automated", cm.exception.message)

    def test_http_error_429_is_blocked_message(self):
        err = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        with self._mock(side_effect=err):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("blocks automated", cm.exception.message)

    def test_http_error_500_is_generic_message(self):
        err = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        with self._mock(side_effect=err):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("(500)", cm.exception.message)

    def test_url_error(self):
        with self._mock(side_effect=urllib.error.URLError("boom")):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("internet connection", cm.exception.message)

    def test_timeout(self):
        with self._mock(side_effect=TimeoutError()):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("took too long", cm.exception.message)

    def test_malformed_missing_data_key(self):
        with self._mock(return_value={"errors": [{"message": "oops"}]}):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)
        self.assertIn("unexpected", cm.exception.message)

    def test_malformed_top_level_not_a_dict(self):
        with self._mock(return_value=["not", "a", "dict"]):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)

    def test_malformed_question_not_a_dict(self):
        with self._mock(return_value={"data": {"question": "surprise-string"}}):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)

    def test_malformed_empty_content_non_premium(self):
        with self._mock(return_value=self._question(content="")):
            with self.assertRaises(leetcode_fetch.FetchError) as cm:
                leetcode_fetch.fetch_statement("x")
        self.assertEqual(cm.exception.status, 502)

    def test_payload_sent_to_the_seam(self):
        captured = {}

        def fake_post(payload, timeout):
            captured["payload"] = payload
            captured["timeout"] = timeout
            return self._question()

        with self._mock(side_effect=fake_post):
            leetcode_fetch.fetch_statement("sum-pairs")
        self.assertEqual(captured["payload"]["variables"], {"titleSlug": "sum-pairs"})
        self.assertIn("questionContent", captured["payload"]["query"])
        self.assertEqual(captured["timeout"], leetcode_fetch.TIMEOUT)


if __name__ == "__main__":
    unittest.main()
