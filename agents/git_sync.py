"""Make sure the local clone has the commit a failed CI run points at, before repairing.

The repair agent (``patcher.Repairer``) reads the failing commit's diff and files from the
LOCAL clone only. CI runs on GitHub, so the worker's clone can be behind. If the commit is
missing, repair would fail with a confusing "patch failed verification". Fetching first
turns that into either a working repair or a clear, retryable error.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger("agents.git_sync")

_SHA = re.compile(r"[0-9a-fA-F]{7,64}")


class CommitUnavailable(RuntimeError):
    """The commit is not in the local clone even after fetching (retry later)."""


def _git(repo_path: str | Path, *args: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def has_commit(repo_path: str | Path, sha: str, *, timeout: float = 20) -> bool:
    if not _SHA.fullmatch(sha or ""):
        return False
    try:
        return _git(repo_path, "cat-file", "-e", f"{sha}^{{commit}}", timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_commit(repo_path: str | Path, sha: str, *, remote: str = "origin",
                  timeout: float = 120) -> bool:
    """True if ``sha`` is present locally, fetching ``remote`` once if it is not.
    Never raises: a failed fetch just returns False. Only reads the remote; it does not
    change your checked-out branch or working tree."""
    if has_commit(repo_path, sha):
        return True
    log.info("commit %s not in local clone; fetching %s", sha[:7], remote)
    try:
        _git(repo_path, "fetch", "--quiet", remote, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("git fetch failed: %s", exc)
        return False
    return has_commit(repo_path, sha)


class SyncingRepairer:
    """Wraps a repairer so the failing commit is fetched first."""

    def __init__(self, inner: Any, repo_path: str | Path, *, remote: str = "origin"):
        self.inner = inner
        self.repo_path = repo_path
        self.remote = remote

    def repair(self, failed_run: dict, similar: list[dict]) -> dict:
        sha = failed_run.get("commit_sha", "")
        if not ensure_commit(self.repo_path, sha, remote=self.remote):
            raise CommitUnavailable(
                f"commit {sha[:7]} is not in the local clone at {self.repo_path} "
                f"and `git fetch {self.remote}` did not bring it in"
            )
        return self.inner.repair(failed_run, similar)
