"""Stand-ins for teammates' modules so the workflow can be run end-to-end now.

FakeGitHub persists "PRs" to a JSON file, so a crash + restart across two
processes behaves like the real thing (GitHub remembers the PR, we don't).
Swap for Hamza's GitHubClient / Faiz's Repairer once they land.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class FakeGitHub:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self._prs: dict[str, dict] = {}
        self.create_calls = 0

    def _load(self) -> dict:
        if self.path and self.path.exists():
            return json.loads(self.path.read_text())
        return self._prs

    def _save(self, prs: dict) -> None:
        self._prs = prs
        if self.path:
            self.path.write_text(json.dumps(prs, indent=2))

    def find_pr(self, repo: str, branch: str) -> dict | None:
        return self._load().get(f"{repo}:{branch}")

    def open_draft_pr(self, repo, branch, base_sha, title, body, patch) -> dict:
        prs = self._load()
        key = f"{repo}:{branch}"
        if key in prs:  # real client must be idempotent on branch too
            return prs[key]
        self.create_calls += 1
        number = 100 + len(prs) + 1
        prs[key] = {"number": number, "url": f"https://github.com/{repo}/pull/{number}",
                    "branch": branch, "title": title, "draft": True}
        self._save(prs)
        return prs[key]


class FakeRepairer:
    def __init__(self, passes: bool = True):
        self.passes = passes
        self.seen_similar: list = []

    def repair(self, failed_run: dict, similar: list[dict]) -> dict:
        self.seen_similar = similar
        hint = similar[0]["summary"] if similar else "no prior incidents"
        return {
            "tests_passed": self.passes,
            "test_output": "5 passed in 0.12s" if self.passes else "1 failed, 4 passed",
            "diff": "--- a/shopping/cart.py\n+++ b/shopping/cart.py\n-    return total\n"
                    "+    return round(total * (1 - discount), 2)\n",
            "files": {"shopping/cart.py": "# patched\n"},
            "changed_files": ["shopping/cart.py"],
            "summary": f"Apply discount before rounding (informed by: {hint})",
            "root_cause": "discount not applied in cart total",
        }


SAMPLE_RUN = {
    "repo": "faizm10/agentic-mongodb",
    "app": "shopping",
    "run_id": 9_000_001,
    "run_attempt": 1,
    "commit_sha": "abc1234def5678",
    "failed_tests": ["shopping/tests/test_cart.py::test_discount_applied"],
    "failed_logs": (
        "FAILED shopping/tests/test_cart.py::test_discount_applied - "
        "AssertionError: assert 100.0 == 90.0\n"
        "E   where 100.0 = Cart.total()\n1 failed, 4 passed in 0.10s"
    ),
}
