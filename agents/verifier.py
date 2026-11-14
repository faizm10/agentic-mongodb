"""Reproduce a CI failure at the failed commit, apply a patch, and rerun the app's tests.

Isolation model:
- The failed commit is exported with ``git archive`` into a fresh temp directory.
  The developer's checkout is only read: no worktree, checkout, stash or index change.
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
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
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


def verify_patch(
    app: AppConfig | str,
    repo_path: str,
    commit_sha: str,
    diff: str,
    failing_tests: list[str],
) -> RepairResult:
    app = load_app_config(app) if isinstance(app, str) else app

    def needs_review(reason: str, **fields) -> RepairResult:
        return RepairResult(
            outcome="needs_review", reason=reason, app=app.name, commit_sha=commit_sha,
            patch=diff or None, **fields,
        )

    patch = validate_patch(diff, app)
    if not patch.accepted:
        return needs_review(f"patch rejected: {patch.rejection_reason}")
    if not _SHA_RE.match(commit_sha):
        return needs_review(f"invalid commit sha {commit_sha!r}")
    tests, bad = _validate_node_ids(failing_tests, app)
    if bad:
        return needs_review(f"refusing unsafe or out-of-scope test ids: {bad}")
    if not tests:
        return needs_review("no failing test ids to reproduce")

    with tempfile.TemporaryDirectory(prefix="opsrelay-verify-") as tmp:
        tmp_path = Path(tmp)
        workspace = tmp_path / "repo"
        try:
            _export_commit(repo_path, commit_sha, workspace)
        except (subprocess.CalledProcessError, tarfile.TarError) as exc:
            return needs_review(f"could not export commit {commit_sha}: {_describe(exc)}")
        app_dir = workspace / app.root
        if not app_dir.is_dir():
            return needs_review(f"app root {app.root!r} does not exist at {commit_sha}")
        env = _sandbox_env(tmp_path)

        baseline = _run_pytest(app, app_dir, tests, env)
        if not _reproduced(baseline):
            return needs_review(
                "baseline did not reproduce the CI failure", baseline=baseline,
                changed_files=patch.changed_files,
            )

        applied = _git(workspace, "apply", "--whitespace=nowarn", "-", input=diff, check=False)
        if applied.returncode != 0:
            return needs_review(
                f"patch does not apply at {commit_sha}: {applied.stderr.strip()[:500]}",
                baseline=baseline, changed_files=patch.changed_files,
            )
        actual = _git(workspace, "diff", "--name-only").stdout.split()
        if sorted(actual) != sorted(patch.changed_files):
            return needs_review(
                f"applied patch changed {actual}, expected {patch.changed_files}",
                baseline=baseline, changed_files=actual,
            )

        after = _run_pytest(app, app_dir, tests, env)
        suite = _run_pytest(app, app_dir, [app.test_dir], env)

    fields = dict(
        baseline=baseline, reproduce_after_patch=after, patched_suite=suite,
        changed_files=patch.changed_files,
    )
    if not after.passed:
        return needs_review("the originally failing tests still fail after the patch", **fields)
    if not suite.passed:
        reason = "app test suite timed out" if suite.timed_out else "app test suite fails after the patch"
        return needs_review(reason, **fields)
    return RepairResult(
        outcome="verified",
        reason=f"{len(tests)} failing test(s) reproduced, then passed with the full {app.name} suite",
        app=app.name, commit_sha=commit_sha, patch=diff, **fields,
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
