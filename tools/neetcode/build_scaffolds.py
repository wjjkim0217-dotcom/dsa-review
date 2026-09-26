"""Build the starter code ("scaffolds") for the NeetCode 150 problems.

For each problem in app/neetcode150.json this downloads NeetCode's accepted Python
solution (github.com/neetcode-gh/leetcode, MIT license), keeps only the class and
method *headers* - the part LeetCode shows you before you start - and replaces the
bodies with `pass`. The result is stored as a "starter" field on each problem in
app/neetcode150.json, which the app loads into the editor.

The design problems (LRU Cache, Min Stack, Trie, ...) are written by hand below:
NeetCode's solutions for those add their own helper classes and methods, which
aren't part of LeetCode's scaffold and would give the approach away.

Run from the project folder (needs internet):
    python tools/neetcode/build_scaffolds.py
Downloaded files are cached in tools/neetcode/.cache/ (safe to delete).
"""
from __future__ import annotations

import ast
import copy
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "app" / "neetcode150.json"
CACHE = Path(__file__).resolve().parent / ".cache"
RAW = "https://raw.githubusercontent.com/neetcode-gh/leetcode/main/python/"

TRY_IT = "# Try it (Ctrl+Enter to run):\n"

# LeetCode's usual "Definition for ..." comments. ListNode and TreeNode are also
# built into the app's Run button, so they stay commented out, like on LeetCode.
DEFINITIONS = {
    "ListNode": (
        "# Definition for singly-linked list.\n"
        "# class ListNode:\n"
        "#     def __init__(self, val=0, next=None):\n"
        "#         self.val = val\n"
        "#         self.next = next\n"
    ),
    "TreeNode": (
        "# Definition for a binary tree node.\n"
        "# class TreeNode:\n"
        "#     def __init__(self, val=0, left=None, right=None):\n"
        "#         self.val = val\n"
        "#         self.left = left\n"
        "#         self.right = right\n"
    ),
}
# Problem-specific classes the Run button doesn't provide (uncomment to test locally).
NODE_DEFINITIONS = {
    "copy-list-with-random-pointer": (
        "# Definition for a Node (uncomment to test locally).\n"
        "# class Node:\n"
        "#     def __init__(self, x: int, next: 'Node' = None, random: 'Node' = None):\n"
        "#         self.val = int(x)\n"
        "#         self.next = next\n"
        "#         self.random = random\n"
    ),
    "clone-graph": (
        "# Definition for a Node (uncomment to test locally).\n"
        "# class Node:\n"
        "#     def __init__(self, val = 0, neighbors = None):\n"
        "#         self.val = val\n"
        "#         self.neighbors = neighbors if neighbors is not None else []\n"
    ),
}



def design(class_name: str, methods: list[str], usage: list[str], prefix: str = "") -> str:
    """A LeetCode-style design-problem scaffold: the class, its methods, and the
    "will be instantiated and called as such" usage comment."""
    body = "\n\n".join(f"    {sig}\n        pass" for sig in methods)
    usage_text = "".join(f"# {line}\n" for line in usage)
    return (f"{prefix}{'' if not prefix else chr(10)}class {class_name}:\n\n{body}\n\n\n"
            f"# Your {class_name} object will be instantiated and called as such:\n{usage_text}")


# Hand-written scaffolds, matching LeetCode's (keyed by LeetCode slug).
OVERRIDES = {
    "min-stack": design("MinStack", [
        "def __init__(self):",
        "def push(self, val: int) -> None:",
        "def pop(self) -> None:",
        "def top(self) -> int:",
        "def getMin(self) -> int:",
    ], ["obj = MinStack()", "obj.push(val)", "obj.pop()", "param_3 = obj.top()", "param_4 = obj.getMin()"]),
    "time-based-key-value-store": design("TimeMap", [
        "def __init__(self):",
        "def set(self, key: str, value: str, timestamp: int) -> None:",
        "def get(self, key: str, timestamp: int) -> str:",
    ], ["obj = TimeMap()", "obj.set(key,value,timestamp)", "param_2 = obj.get(key,timestamp)"]),
    "lru-cache": design("LRUCache", [
        "def __init__(self, capacity: int):",
        "def get(self, key: int) -> int:",
        "def put(self, key: int, value: int) -> None:",
    ], ["obj = LRUCache(capacity)", "param_1 = obj.get(key)", "obj.put(key,value)"]),
    "serialize-and-deserialize-binary-tree": design("Codec", [
        "def serialize(self, root: Optional[TreeNode]) -> str:",
        "def deserialize(self, data: str) -> Optional[TreeNode]:",
    ], ["ser = Codec()", "deser = Codec()", "ans = deser.deserialize(ser.serialize(root))"],
        prefix=DEFINITIONS["TreeNode"]),
    "implement-trie-prefix-tree": design("Trie", [
        "def __init__(self):",
        "def insert(self, word: str) -> None:",
        "def search(self, word: str) -> bool:",
        "def startsWith(self, prefix: str) -> bool:",
    ], ["obj = Trie()", "obj.insert(word)", "param_2 = obj.search(word)", "param_3 = obj.startsWith(prefix)"]),
    "design-add-and-search-words-data-structure": design("WordDictionary", [
        "def __init__(self):",
        "def addWord(self, word: str) -> None:",
        "def search(self, word: str) -> bool:",
    ], ["obj = WordDictionary()", "obj.addWord(word)", "param_2 = obj.search(word)"]),
    "kth-largest-element-in-a-stream": design("KthLargest", [
        "def __init__(self, k: int, nums: List[int]):",
        "def add(self, val: int) -> int:",
    ], ["obj = KthLargest(k, nums)", "param_1 = obj.add(val)"]),
    "design-twitter": design("Twitter", [
        "def __init__(self):",
        "def postTweet(self, userId: int, tweetId: int) -> None:",
        "def getNewsFeed(self, userId: int) -> List[int]:",
        "def follow(self, followerId: int, followeeId: int) -> None:",
        "def unfollow(self, followerId: int, followeeId: int) -> None:",
    ], ["obj = Twitter()", "obj.postTweet(userId,tweetId)", "param_2 = obj.getNewsFeed(userId)",
        "obj.follow(followerId,followeeId)", "obj.unfollow(followerId,followeeId)"]),
    "find-median-from-data-stream": design("MedianFinder", [
        "def __init__(self):",
        "def addNum(self, num: int) -> None:",
        "def findMedian(self) -> float:",
    ], ["obj = MedianFinder()", "obj.addNum(num)", "param_2 = obj.findMedian()"]),
    "detect-squares": design("DetectSquares", [
        "def __init__(self):",
        "def add(self, point: List[int]) -> None:",
        "def count(self, point: List[int]) -> int:",
    ], ["obj = DetectSquares()", "obj.add(point)", "param_2 = obj.count(point)"]),
    # NeetCode's free version of this (premium on LeetCode) uses one Solution class
    # with both methods.
    "encode-and-decode-strings": (
        "class Solution:\n\n"
        "    def encode(self, strs: List[str]) -> str:\n        pass\n\n"
        "    def decode(self, s: str) -> List[str]:\n        pass\n\n\n"
        + TRY_IT + "# s = Solution()\n# print(s.decode(s.encode([\"neet\", \"code\"])))\n"
    ),
}

# Solution-method signatures written out by hand: either NeetCode's file doesn't
# parse as-is, or its signature differs from LeetCode's (NeetCode's own site uses
# different names/types for a few premium problems; the app links to LeetCode).
SIGNATURE_OVERRIDES = {
    "number-of-islands": "def numIslands(self, grid: List[List[str]]) -> int:",
    "graph-valid-tree": "def validTree(self, n: int, edges: List[List[int]]) -> bool:",
    "meeting-rooms": "def canAttendMeetings(self, intervals: List[List[int]]) -> bool:",
    "meeting-rooms-ii": "def minMeetingRooms(self, intervals: List[List[int]]) -> int:",
    "walls-and-gates": "def wallsAndGates(self, rooms: List[List[int]]) -> None:",
    "partition-labels": "def partitionLabels(self, s: str) -> List[int]:",
    "copy-list-with-random-pointer": "def copyRandomList(self, head: 'Optional[Node]') -> 'Optional[Node]':",
    "clone-graph": "def cloneGraph(self, node: Optional['Node']) -> Optional['Node']:",
}

# LeetCode writes linked-list/tree parameters as Optional[ListNode] / Optional[TreeNode]
# (NeetCode usually leaves out the Optional). Exceptions where LeetCode doesn't:
NOT_OPTIONAL = {"count-good-nodes-in-binary-tree"}


class _OptionalNodes(ast.NodeTransformer):
    """Rewrites a bare `ListNode`/`TreeNode` annotation (also inside List[...]) to
    `Optional[...]`, like LeetCode's own scaffolds."""

    def visit_Subscript(self, node):
        # Don't wrap what's already Optional[...]; do recurse into List[...] etc.
        if isinstance(node.value, ast.Name) and node.value.id == "Optional":
            return node
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in ("ListNode", "TreeNode"):
            return ast.Subscript(value=ast.Name(id="Optional", ctx=ast.Load()), slice=node, ctx=ast.Load())
        return node


def fetch(code_file: str) -> str:
    CACHE.mkdir(exist_ok=True)
    cached = CACHE / code_file
    if not cached.exists():
        with urllib.request.urlopen(RAW + code_file, timeout=30) as resp:
            cached.write_bytes(resp.read())
    return cached.read_text(encoding="utf-8")


def header_only(fn: ast.FunctionDef) -> str:
    """'def name(self, a: T) -> R:' - the method's signature without its body."""
    stub = copy.copy(fn)  # shallow copy: keeps name/args/return type, new body below
    stub.body = [ast.Pass()]
    stub.decorator_list = []
    return ast.unparse(stub).split("\n")[0]


def solution_signature(slug: str, source: str) -> str:
    """The main method of the first `class Solution` (its first public method;
    NeetCode sometimes adds helper methods after it, or a second approach)."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Solution":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and not item.name.startswith("_"):
                    if slug not in NOT_OPTIONAL:
                        fixer = _OptionalNodes()
                        for arg in item.args.args:
                            if arg.annotation is not None:
                                arg.annotation = fixer.visit(arg.annotation)
                        if item.returns is not None:
                            item.returns = fixer.visit(item.returns)
                    return header_only(item)
    raise ValueError("no Solution method found")


def solution_scaffold(slug: str, signature: str) -> str:
    """Wrap one method signature in LeetCode's usual layout, with the matching
    "Definition for ..." comment and a commented-out example call."""
    prefix = NODE_DEFINITIONS.get(slug, "")
    if not prefix:
        for name, text in DEFINITIONS.items():
            if name in signature:
                prefix = text
                break
    method = signature.split("(")[0].replace("def ", "").strip()
    body = "        pass\n"
    if signature.endswith("-> None:"):
        # LeetCode's note on in-place problems, naming the first parameter.
        first_param = signature.split("(", 1)[1].split(",")[1].split(":")[0].strip()
        body = (f'        """\n        Do not return anything, modify {first_param} in-place instead.\n'
                f'        """\n')
    return (f"{prefix}{chr(10) if prefix else ''}class Solution:\n"
            f"    {signature}\n{body}\n\n"
            f"{TRY_IT}# print(Solution().{method}(...))\n")


def build() -> int:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    for p in data["problems"]:
        slug = p["slug"]
        if OVERRIDES.get(slug):
            p["starter"] = OVERRIDES[slug]
            continue
        if slug in SIGNATURE_OVERRIDES:
            p["starter"] = solution_scaffold(slug, SIGNATURE_OVERRIDES[slug])
            continue
        code_file = p["solution_url"].rsplit("/", 1)[1]
        p["starter"] = solution_scaffold(slug, solution_signature(slug, fetch(code_file)))
    # Every scaffold must at least be valid Python.
    for p in data["problems"]:
        compile(p["starter"], f"<starter {p['slug']}>", "exec")
    DATA.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote starter code for {len(data['problems'])} problems to {DATA}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
