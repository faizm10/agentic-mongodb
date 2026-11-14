"""Reproduce a CI failure at the failed commit, apply candidate patches, rerun the app's tests.

Isolation model:
- The failed commit is exported with ``git archive`` into a fresh temp directory.
  The developer's checkout is only read: no worktree, checkout, stash or index change.
- Each candidate patch is applied to its own copy of that pristine export.
- Tests run with a scrubbed environment (PATH, a temp HOME/TMPDIR and locale only),
  so API keys, MONGODB_URI, GITHUB_TOKEN etc. are never visible to the code under test.
- Commands are fixed by apps.toml; only validated pytest node ids are appended.
- Every run has a timeout and its whole process group is killed on expiry.
- The temp directory is deleted afterwards.

This is process-level isolation, not a container: tests can still reach the network.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .contracts import AppConfig, RepairResult, TestRun, load_app_config
from .patcher import validate_patch

OUTPUT_TAIL_CHARS = 4_000

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_NODE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.py(::[A-Za-z0-9_\[\]\-.]+)*$")

_GIT_ENV = {
    "PATH": os.environ.get("PATH", os.defpath),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "LANG": "C",
}
_GIT_ISOLATION = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "commit.gpgsign=false",
    "-c", "user.name=opsrelay-verifier",
    "-c", "user.email=verifier@opsrelay.invalid",
]


class VerificationError(Exception):
    """The failure cannot be verified at all; the message is the review reason."""


@dataclass
class Baseline:
    runs: list[TestRun]

    @property
    def reproduced(self) -> bool:
        return all(_reproduced(run) for run in self.runs)

    @property
    def flaky(self) -> bool:
        return any(_reproduced(run) for run in self.runs) and any(run.passed for run in self.runs)


@dataclass
class CandidateCheck:
    passed: bool
    reason: str
    changed_files: list[str] = field(default_factory=list)
    changed_lines: int = 0
    reproduce_runs: list[TestRun] = field(default_factory=list)
    suite: TestRun | None = None


class VerificationSession:
    """Export the failed commit once, then reproduce and check candidates against it.

    Use as a context manager; raises VerificationError on entry if the commit,
    app root or test ids cannot be used.
    """

    def __init__(self, app: AppConfig, repo_path: str, commit_sha: str, failing_tests: list[str]):
        self.app = app
        self.repo_path = repo_path
        self.commit_sha = commit_sha
        self.failing_tests = failing_tests
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._candidates = 0

    def __enter__(self) -> VerificationSession:
        if not _SHA_RE.match(self.commit_sha):
            raise VerificationError(f"invalid commit sha {self.commit_sha!r}")
        tests, bad = _validate_node_ids(self.failing_tests, self.app)
        if bad:
            raise VerificationError(f"refusing unsafe or out-of-scope test ids: {bad}")
        if not tests:
            raise VerificationError("no failing test ids to reproduce")
        self.tests = tests

        self._tmp = tempfile.TemporaryDirectory(prefix="opsrelay-verify-")
        self.root = Path(self._tmp.name)
        self.pristine = self.root / "pristine"
        try:
            _export_commit(self.repo_path, self.commit_sha, self.pristine)
        except (subprocess.CalledProcessError, tarfile.TarError) as exc:
            self.__exit__(None, None, None)
            raise VerificationError(f"could not export commit {self.commit_sha}: {_describe(exc)}") from exc
        if not (self.pristine / self.app.root).is_dir():
            self.__exit__(None, None, None)
            raise VerificationError(f"app root {self.app.root!r} does not exist at {self.commit_sha}")
        self.env = _sandbox_env(self.root)
        return self

    def __exit__(self, *exc) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def reproduce(self) -> Baseline:
        """Run the failing tests ``baseline_runs`` times on a copy of the original code."""
        workspace = self._fresh_copy("baseline")
        app_dir = workspace / self.app.root
        return Baseline([self._pytest(app_dir, self.tests) for _ in range(max(1, self.app.baseline_runs))])

    def check(self, diff: str) -> CandidateCheck:
        """Apply ``diff`` to a fresh copy and rerun the failing tests and the full suite."""
        patch = validate_patch(diff, self.app)
        if not patch.accepted:
            return CandidateCheck(False, f"patch rejected: {patch.rejection_reason}")

        self._candidates += 1
        workspace = self._fresh_copy(f"candidate-{self._candidates}")
        fields = dict(changed_files=patch.changed_files, changed_lines=patch.changed_lines)
        applied = _git(workspace, "apply", "--whitespace=nowarn", "-", input=diff, check=False)
        if applied.returncode != 0:
            return CandidateCheck(
                False, f"patch does not apply at {self.commit_sha}: {applied.stderr.strip()[:500]}", **fields
            )
        actual = _git(workspace, "diff", "--name-only").stdout.split()
        if sorted(actual) != sorted(patch.changed_files):
            return CandidateCheck(False, f"applied patch changed {actual}, expected {patch.changed_files}", **fields)

        app_dir = workspace / self.app.root
        runs: list[TestRun] = []
        for _ in range(max(1, self.app.baseline_runs)):
            runs.append(self._pytest(app_dir, self.tests))
            if not runs[-1].passed:
                break
        if not runs[-1].passed:
            reason = "patched run timed out" if runs[-1].timed_out else "the originally failing tests still fail after the patch"
            return CandidateCheck(False, reason, reproduce_runs=runs, **fields)

        suite = self._pytest(app_dir, [self.app.test_dir])
        if not suite.passed:
            reason = "app test suite timed out" if suite.timed_out else "app test suite fails after the patch"
            return CandidateCheck(False, reason, reproduce_runs=runs, suite=suite, **fields)
        return CandidateCheck(
            True,
            f"{len(self.tests)} failing test(s) now pass on {len(runs)} rerun(s), plus the full {self.app.name} suite",
            reproduce_runs=runs, suite=suite, **fields,
        )

    def _fresh_copy(self, name: str) -> Path:
        dest = self.root / name
        shutil.copytree(self.pristine, dest, symlinks=True)
        return dest

    def _pytest(self, cwd: Path, targets: list[str]) -> TestRun:
        return _run_pytest(self.app, cwd, targets, self.env)


def verify_patch(
    app: AppConfig | str,
    repo_path: str,
    commit_sha: str,
    diff: str,
    failing_tests: list[str],
) -> RepairResult:
    """Verify a single externally supplied patch (no model involved)."""
    app = load_app_config(app) if isinstance(app, str) else app

    def result(outcome: str, reason: str, **fields) -> RepairResult:
        return RepairResult(outcome=outcome, reason=reason, app=app.name, commit_sha=commit_sha, **fields)

    patch = validate_patch(diff, app)
    if not patch.accepted:
        return result("needs_review", f"patch rejected: {patch.rejection_reason}", patch=diff or None)
    try:
        with VerificationSession(app, repo_path, commit_sha, failing_tests) as session:
            baseline = session.reproduce()
            if not baseline.reproduced:
                reason = (
                    "flaky: the failing tests passed on at least one baseline rerun"
                    if baseline.flaky else "baseline did not reproduce the CI failure"
                )
                return result("needs_review", reason, flaky=baseline.flaky, baseline=baseline.runs, patch=diff)
            check = session.check(diff)
    except VerificationError as exc:
        return result("needs_review", str(exc), patch=diff)

    return result(
        "verified" if check.passed else "needs_review", check.reason,
        patch=diff, changed_files=check.changed_files, baseline=baseline.runs,
        reproduce_after_patch=check.reproduce_runs, patched_suite=check.suite,
    )


def _validate_node_ids(node_ids: list[str], app: AppConfig) -> tuple[list[str], list[str]]:
    test_root = app.test_dir.rstrip("/") + "/"
    good, bad = [], []
    for node in dict.fromkeys(node_ids):
        path = node.split("::", 1)[0]
        ok = _NODE_ID_RE.match(node) and path.startswith(test_root) and ".." not in path.split("/")
        (good if ok else bad).append(node)
    return good, bad


def _export_commit(repo_path: str, commit_sha: str, dest: Path) -> None:
    """Copy the tree at commit_sha into dest and give it a throwaway git repo."""
    archive = subprocess.run(
        ["git", "-C", repo_path, "archive", "--format=tar", f"{commit_sha}^{{commit}}"],
        capture_output=True, check=True,
    )
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "--no-verify", "-m", f"failed commit {commit_sha}")


def _git(cwd: Path, *args: str, input: str | None = None, check: bool = True):
    return subprocess.run(
        ["git", *_GIT_ISOLATION, *args], cwd=cwd, input=input, text=True,
        capture_output=True, check=check, env=_GIT_ENV,
    )


def _sandbox_env(tmp: Path) -> dict[str, str]:
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "NO_COLOR": "1",
    }


def _run_pytest(app: AppConfig, cwd: Path, targets: list[str], env: dict[str, str]) -> TestRun:
    command = [sys.executable, "-m", "pytest", *app.pytest_args, "--", *targets]
    started = time.monotonic()
    proc = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=app.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        output += f"\n[verifier] timed out after {app.timeout_seconds}s"
    return TestRun(
        command=["python", *command[1:]],
        returncode=None if timed_out else proc.returncode,
        passed=not timed_out and proc.returncode == 0,
        timed_out=timed_out,
        duration_seconds=round(time.monotonic() - started, 2),
        output_tail=output[-OUTPUT_TAIL_CHARS:],
    )


def _reproduced(run: TestRun) -> bool:
    # 1 = tests failed; 2 with a collection error covers import-time breakage.
    # Other codes (4 usage error, 5 no tests collected) mean we did not reproduce it.
    return run.returncode == 1 or (run.returncode == 2 and "ERROR collecting" in run.output_tail)


def _describe(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return stderr.strip()[:300] or str(exc)
    return str(exc)
