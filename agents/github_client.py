"""GitHub REST API client (Developer 1): authentication, read calls and thin write calls.

Role in the project
-------------------
This is the only module that talks to GitHub. The monitor uses it to find failed
CI runs, and ``get_failure_context`` uses it to gather everything Developer 2's AI
needs (logs, commit, source files). The branch / commit / PR methods at the bottom of
``GitHubClient`` are deliberately thin: the safety logic (check before write, verify
after write, never duplicate) lives in ``github_publisher.py``.

Design rules
------------
* Credentials come from environment variables (or a root-level ``.env``) - never hardcoded.
* Reads (GET) are safe to repeat, so they are retried automatically on timeouts / 5xx.
* Errors are typed so callers can tell "GitHub said no" from "we don't know what happened":
    GitHubAuthError    401 / missing permission
    GitHubNotFound     404
    GitHubRateLimited  rate limit hit
    GitHubConflict     409
    GitHubValidationError  422 (e.g. "Reference already exists")
    GitHubUnavailable  timeout, network error, 5xx  -> OUTCOME UNKNOWN for writes
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .github_types import (
    BranchInfo,
    ChangedFile,
    CommitInfo,
    FailedJob,
    FailureContext,
    PullRequestInfo,
    RepoFile,
    app_root,
    application_for_workflow_path,
    make_incident_id,
)

log = logging.getLogger("agents.github")

API_VERSION = "2022-11-28"
DEFAULT_MAX_LOG_CHARS = 20_000      # total log budget handed to the LLM
MAX_JOBS_IN_CONTEXT = 3             # how many failed jobs' logs we include
MAX_SOURCE_FILES = 8                # how many source files we attach
MAX_SOURCE_CHARS = 40_000           # per-file content cap
MAX_PATCH_CHARS = 6_000             # per-file diff cap
FAILED_CONCLUSIONS = {"failure", "timed_out"}


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class GitHubError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class GitHubConfigError(GitHubError):
    """Missing / invalid environment configuration."""


class GitHubAuthError(GitHubError):
    """401, or 403 because the token lacks a permission."""


class GitHubNotFound(GitHubError):
    """404 (also returned for private repos the token cannot see)."""


class GitHubRateLimited(GitHubError):
    """Primary or secondary rate limit."""


class GitHubConflict(GitHubError):
    """409."""


class GitHubValidationError(GitHubError):
    """422: GitHub understood the request but refused it (already exists, no diff, bad sha...)."""


class GitHubUnavailable(GitHubError):
    """Timeout, network failure or 5xx. For a write, the outcome is AMBIGUOUS."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no extra dependency). Real env vars always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(repr=False)  # repr=False so the token never leaks into logs
    owner: str
    repo: str = "agentic-mongodb"
    base_branch: str = "main"
    api_url: str = "https://api.github.com"

    @classmethod
    def from_env(cls) -> "GitHubConfig":
        # repo root = parent of the agents/ package
        _load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        missing = [n for n in ("GITHUB_TOKEN", "GITHUB_OWNER") if not os.getenv(n)]
        if missing:
            raise GitHubConfigError(
                f"Missing environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )
        return cls(
            token=os.environ["GITHUB_TOKEN"],
            owner=os.environ["GITHUB_OWNER"],
            repo=os.getenv("GITHUB_REPO", "agentic-mongodb"),
            base_branch=os.getenv("GITHUB_BASE_BRANCH", "main"),
        )


# --------------------------------------------------------------------------- #
# Log helpers (pure functions, easy to test)
# --------------------------------------------------------------------------- #

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TIMESTAMP_RE = re.compile(r"^﻿?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ", re.M)


def clean_logs(text: str, max_chars: int = DEFAULT_MAX_LOG_CHARS) -> str:
    """Strip colour codes + per-line timestamps and keep the TAIL (errors are at the end)."""
    text = text.replace("\r\n", "\n")
    text = _ANSI_RE.sub("", text)
    text = _TIMESTAMP_RE.sub("", text)
    if max_chars and len(text) > max_chars:
        tail = text[-max_chars:]
        newline = tail.find("\n")
        if 0 <= newline < 500:          # start on a clean line boundary
            tail = tail[newline + 1:]
        text = f"[... {len(text) - len(tail)} characters truncated from the start ...]\n{tail}"
    return text


_PATH_RE = re.compile(r"([\w@./\\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs))(?=[:\",)\s]|$)")


def extract_log_paths(logs: str, application: str, limit: int = 10) -> list[str]:
    """Guess repo-relative source paths mentioned in a log (tracebacks, jest/pytest output).

    The guesses are only candidates - callers must confirm they exist in the repo.
    """
    marker = f"{application}/"
    found: list[str] = []
    for match in _PATH_RE.finditer(logs):
        token = match.group(1).replace("\\", "/")
        if "node_modules" in token or "site-packages" in token:
            continue
        if token.startswith(marker):
            path = token
        elif f"/{marker}" in token:                     # absolute runner path
            path = token[token.index(f"/{marker}") + 1:]
        elif token.startswith("/") or token.startswith(".."):
            continue                                    # absolute path outside the repo
        else:                                           # relative to the app's working dir
            path = marker + token.lstrip("./")
        if path not in found:
            found.append(path)
        if len(found) >= limit:
            break
    return found


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class GitHubClient:
    def __init__(
        self,
        config: Optional[GitHubConfig] = None,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 20.0,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
    ):
        self.config = config or GitHubConfig.from_env()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._http = httpx.Client(
            base_url=self.config.api_url,
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "agentic-mongodb-incident-agent",
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
            # Log downloads redirect to a signed blob URL; httpx drops the
            # Authorization header automatically when the host changes.
            follow_redirects=True,
            transport=transport,
        )

    # -- plumbing ---------------------------------------------------------- #

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def _repo(self) -> str:
        return f"/repos/{self.config.owner}/{self.config.repo}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
        accept: Optional[str] = None,
        retry: Optional[bool] = None,
    ) -> httpx.Response:
        """Send a request. GETs are retried on timeouts/5xx; writes never are (Phase 2
        handles ambiguous writes by *checking GitHub*, not by blindly re-sending)."""
        should_retry = (method == "GET") if retry is None else retry
        attempts = (self._max_retries if should_retry else 0) + 1
        headers = {"Accept": accept} if accept else None
        last: Optional[GitHubError] = None

        for attempt in range(attempts):
            try:
                resp = self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as exc:  # timeouts, connection resets, DNS ...
                last = GitHubUnavailable(f"{method} {path} failed: {exc!r}")
            else:
                if resp.status_code >= 500:
                    last = GitHubUnavailable(
                        f"{method} {path} -> HTTP {resp.status_code}", resp.status_code
                    )
                else:
                    return self._check(resp, method, path)
            if attempt < attempts - 1:
                time.sleep(self._retry_backoff * (2 ** attempt))
        assert last is not None
        raise last

    @staticmethod
    def _check(resp: httpx.Response, method: str, path: str) -> httpx.Response:
        if resp.status_code < 400:
            return resp
        try:
            body = resp.json()
            message = body.get("message", "")
            errors = body.get("errors")
            if errors:
                message += " " + "; ".join(
                    (e.get("message") or e.get("code") or "") if isinstance(e, dict) else str(e)
                    for e in errors
                )
        except Exception:
            message = resp.text[:200]
        where = f"{method} {path} -> HTTP {resp.status_code}: {message}"
        status = resp.status_code
        if status == 401:
            raise GitHubAuthError(f"{where} (token invalid or expired)", status)
        if status == 429 or (status == 403 and (
            resp.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in message.lower()
        )):
            reset = resp.headers.get("x-ratelimit-reset", "?")
            raise GitHubRateLimited(f"{where} (rate limit resets at epoch {reset})", status)
        if status == 403:
            needed = resp.headers.get("x-accepted-github-permissions", "unknown")
            raise GitHubAuthError(f"{where} (token lacks permission; needs: {needed})", status)
        if status == 404:
            raise GitHubNotFound(where, status)
        if status == 409:
            raise GitHubConflict(where, status)
        if status == 422:
            raise GitHubValidationError(where, status)
        raise GitHubError(where, status)

    def _get_json(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=clean or None).json()

    # -- Task 1: authentication ------------------------------------------- #

    def get_repo(self) -> dict[str, Any]:
        """Repository metadata. Success proves the token + owner + repo are correct."""
        return self._get_json(self._repo)

    def verify_auth(self) -> dict[str, Any]:
        """Check the token and report which read permissions actually work."""
        repo = self.get_repo()
        checks: dict[str, Any] = {}
        probes = {
            "actions_read": lambda: self.list_workflow_runs(per_page=1),
            "contents_read": lambda: self._get_json(
                f"{self._repo}/contents", ref=self.config.base_branch
            ),
        }
        for name, probe in probes.items():
            try:
                probe()
                checks[name] = True
            except GitHubAuthError:
                checks[name] = False
            except GitHubError as exc:
                checks[name] = f"error: {exc}"
        return {
            "full_name": repo.get("full_name"),
            "private": repo.get("private"),
            "default_branch": repo.get("default_branch"),
            "token_permissions": repo.get("permissions"),
            "checks": checks,
            "note": "contents:write and pull_requests:write can only be proven by a real write (Phase 2).",
        }

    # -- Task 2 support: workflow runs ------------------------------------ #

    def list_workflow_runs(
        self,
        *,
        branch: Optional[str] = None,
        status: Optional[str] = None,
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        """Recent workflow runs. ``status="failure"`` filters to failed runs server-side."""
        data = self._get_json(
            f"{self._repo}/actions/runs",
            branch=branch,
            status=status,
            per_page=per_page,
            exclude_pull_requests="true",
        )
        return data.get("workflow_runs", [])

    def get_run(self, run_id: int) -> dict[str, Any]:
        return self._get_json(f"{self._repo}/actions/runs/{run_id}")

    # -- Task 3: logs + failure context ----------------------------------- #

    def get_failed_jobs(self, run_id: int, attempt: Optional[int] = None) -> list[FailedJob]:
        if attempt:
            path = f"{self._repo}/actions/runs/{run_id}/attempts/{attempt}/jobs"
            data = self._get_json(path, per_page=100)
        else:
            data = self._get_json(
                f"{self._repo}/actions/runs/{run_id}/jobs", filter="latest", per_page=100
            )
        jobs = []
        for job in data.get("jobs", []):
            if job.get("conclusion") in FAILED_CONCLUSIONS:
                steps = [
                    s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"
                ]
                jobs.append(
                    FailedJob(
                        job_id=job["id"],
                        name=job["name"],
                        conclusion=job["conclusion"],
                        url=job.get("html_url", ""),
                        failed_steps=steps,
                    )
                )
        return jobs

    def get_job_logs(
        self, job_id: int, *, max_chars: int = DEFAULT_MAX_LOG_CHARS, raw: bool = False
    ) -> str:
        resp = self._request("GET", f"{self._repo}/actions/jobs/{job_id}/logs")
        return resp.text if raw else clean_logs(resp.text, max_chars)

    def get_commit(self, sha: str) -> CommitInfo:
        data = self._get_json(f"{self._repo}/commits/{sha}")
        commit = data.get("commit", {})
        files = [
            ChangedFile(
                path=f["filename"],
                status=f.get("status", ""),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch"),
            )
            for f in data.get("files", [])
        ]
        return CommitInfo(
            sha=data.get("sha", sha),
            message=commit.get("message", ""),
            author=(commit.get("author") or {}).get("name", ""),
            url=data.get("html_url", ""),
            files=files,
        )

    def get_file(self, path: str, ref: Optional[str] = None) -> RepoFile:
        """File contents at ``ref`` (branch or sha). ``.sha`` is the blob sha Phase 2 needs."""
        ref = ref or self.config.base_branch
        url = f"{self._repo}/contents/{path}"
        data = self._get_json(url, ref=ref)
        if isinstance(data, list):
            raise GitHubError(f"{path} is a directory, not a file")
        if data.get("encoding") == "base64" and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:  # files > 1 MB come back without inline content
            content = self._request(
                "GET", url, params={"ref": ref}, accept="application/vnd.github.raw+json"
            ).text
        return RepoFile(path=path, ref=ref, sha=data.get("sha", ""), content=content)

    def get_file_or_none(self, path: str, ref: Optional[str] = None) -> Optional[RepoFile]:
        try:
            return self.get_file(path, ref)
        except GitHubNotFound:
            return None

    def get_failure_context(self, run_id: int, attempt: Optional[int] = None) -> FailureContext:
        """Bundle everything Developer 2 needs to diagnose one failed run."""
        run = self.get_run(run_id)
        attempt = attempt or run.get("run_attempt", 1)
        commit_sha = run["head_sha"]
        application = application_for_workflow_path(run.get("path")) or "unknown"

        # 1. Failed jobs + their logs (tail of each, within one total budget)
        jobs = self.get_failed_jobs(run_id, attempt)
        shown = jobs[:MAX_JOBS_IN_CONTEXT]
        budget = DEFAULT_MAX_LOG_CHARS // max(len(shown), 1)
        parts = []
        for job in shown:
            text = self.get_job_logs(job.job_id, max_chars=budget)
            parts.append(text if len(shown) == 1 else f"=== job: {job.name} ===\n{text}")
        logs = "\n".join(parts)

        # 2. The commit under test and the files it touched
        commit = self.get_commit(commit_sha)
        root = app_root(application)
        prefix = f"{root}/"
        in_app = [f for f in commit.files if f.path.startswith(prefix)]
        changed = in_app or commit.files
        for f in changed:
            if f.patch and len(f.patch) > MAX_PATCH_CHARS:
                f.patch = f.patch[:MAX_PATCH_CHARS] + "\n[... diff truncated ...]"

        # 3. Source files: changed files first, then files named in the log
        candidates = [f.path for f in changed if f.status != "removed"]
        for path in extract_log_paths(logs, root):
            if path not in candidates:
                candidates.append(path)
        sources: list[RepoFile] = []
        for path in candidates:
            if len(sources) >= MAX_SOURCE_FILES:
                break
            repo_file = self.get_file_or_none(path, ref=commit_sha)
            if repo_file is None:
                continue
            if len(repo_file.content) > MAX_SOURCE_CHARS:
                repo_file.content = repo_file.content[:MAX_SOURCE_CHARS]
                repo_file.truncated = True
            sources.append(repo_file)

        return FailureContext(
            incident_id=make_incident_id(run_id, attempt),
            application=application,
            workflow_run_id=run_id,
            run_attempt=attempt,
            commit_sha=commit_sha,
            failed_job=shown[0].name if shown else "(no failed job found)",
            failed_job_id=shown[0].job_id if shown else None,
            failed_jobs=[j.name for j in jobs],
            logs=logs,
            workflow_url=run.get("html_url", ""),
            commit_message=commit.message,
            branch=run.get("head_branch", ""),
            changed_files=changed,
            source_files=sources,
        )

    # -- Phase 2: branches, commits, pull requests (thin wrappers) ----------- #
    # Writes are never auto-retried by ``_request``. github_publisher.py decides what
    # to do after an ambiguous failure by *reading GitHub first*.

    def get_branch(self, name: str) -> Optional[BranchInfo]:
        """The branch and its head sha, or ``None`` if it does not exist."""
        try:
            data = self._get_json(f"{self._repo}/git/ref/heads/{quote(name, safe='/')}")
        except GitHubNotFound:
            return None
        return BranchInfo(name=name, sha=data["object"]["sha"])

    def create_branch(self, name: str, base_sha: str) -> BranchInfo:
        data = self._request(
            "POST", f"{self._repo}/git/refs", json={"ref": f"refs/heads/{name}", "sha": base_sha}
        ).json()
        return BranchInfo(name=name, sha=data["object"]["sha"])

    def get_tree_sha(self, commit_sha: str) -> str:
        """Tree sha of a commit (needed to build a new commit on top of it)."""
        return self._get_json(f"{self._repo}/git/commits/{commit_sha}")["tree"]["sha"]

    def create_tree(self, base_tree: str, files: dict[str, str]) -> str:
        """New tree = ``base_tree`` with ``files`` ({path: full content}) replaced."""
        entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files.items()
        ]
        data = self._request(
            "POST", f"{self._repo}/git/trees", json={"base_tree": base_tree, "tree": entries}
        ).json()
        return data["sha"]

    def create_git_commit(self, message: str, tree_sha: str, parents: list[str]) -> str:
        data = self._request(
            "POST",
            f"{self._repo}/git/commits",
            json={"message": message, "tree": tree_sha, "parents": parents},
        ).json()
        return data["sha"]

    def update_branch(self, name: str, sha: str, *, force: bool = False) -> None:
        """Move a branch to ``sha``. Without ``force`` GitHub rejects non-fast-forwards (422)."""
        self._request(
            "PATCH",
            f"{self._repo}/git/refs/heads/{quote(name, safe='/')}",
            json={"sha": sha, "force": force},
        )

    def compare_files(self, base: str, head: str) -> list[str]:
        """Paths that differ between two commits (renames list both names)."""
        data = self._get_json(f"{self._repo}/compare/{base}...{head}")
        paths: list[str] = []
        for f in data.get("files", []):
            paths.append(f["filename"])
            if f.get("previous_filename"):
                paths.append(f["previous_filename"])
        return paths

    @staticmethod
    def _pr_from(d: dict[str, Any]) -> PullRequestInfo:
        state = "merged" if d.get("merged_at") or d.get("merged") else d.get("state", "open")
        return PullRequestInfo(
            number=d["number"],
            url=d.get("html_url", ""),
            state=state,
            head_ref=d["head"]["ref"],
            head_sha=d["head"]["sha"],
            base_ref=d["base"]["ref"],
            title=d.get("title", ""),
        )

    def find_pr(self, branch: str, state: str = "all") -> Optional[PullRequestInfo]:
        """The pull request opened from ``branch`` (any state), or ``None``."""
        data = self._get_json(
            f"{self._repo}/pulls",
            head=f"{self.config.owner}:{branch}",
            state=state,
            per_page=10,
        )
        for item in data:
            if item["head"]["ref"] == branch:
                return self._pr_from(item)
        return None

    def create_pr(self, branch: str, title: str, body: str, draft: bool = False) -> PullRequestInfo:
        """Open a PR. With ``draft=True`` it is opened as a draft; repositories that do not
        support drafts answer 422 mentioning "draft", and we then open a normal PR (the
        agent still never merges anything itself)."""
        payload = {"title": title, "head": branch, "base": self.config.base_branch, "body": body}
        if draft:
            try:
                return self._pr_from(
                    self._request("POST", f"{self._repo}/pulls", json={**payload, "draft": True}).json()
                )
            except GitHubValidationError as exc:
                if "draft" not in str(exc).lower():
                    raise
                log.warning("repository does not support draft PRs; opening a normal PR")
        return self._pr_from(self._request("POST", f"{self._repo}/pulls", json=payload).json())

    def get_pr(self, number: int) -> PullRequestInfo:
        return self._pr_from(self._get_json(f"{self._repo}/pulls/{number}"))


# --------------------------------------------------------------------------- #
# Module-level convenience functions (the interface other developers import)
# --------------------------------------------------------------------------- #

_default_client: Optional[GitHubClient] = None


def get_client() -> GitHubClient:
    """Lazily-created shared client configured from the environment."""
    global _default_client
    if _default_client is None:
        _default_client = GitHubClient()
    return _default_client


def get_failed_jobs(run_id: int, attempt: Optional[int] = None) -> list[FailedJob]:
    return get_client().get_failed_jobs(run_id, attempt)


def get_job_logs(job_id: int) -> str:
    return get_client().get_job_logs(job_id)


def get_commit(sha: str) -> CommitInfo:
    return get_client().get_commit(sha)


def get_file(path: str, ref: Optional[str] = None) -> RepoFile:
    return get_client().get_file(path, ref)


def get_failure_context(run_id: int, attempt: Optional[int] = None) -> FailureContext:
    return get_client().get_failure_context(run_id, attempt)


def get_branch(name: str) -> Optional[BranchInfo]:
    return get_client().get_branch(name)


def create_branch(name: str, base_sha: str) -> BranchInfo:
    return get_client().create_branch(name, base_sha)


def find_pr(branch: str) -> Optional[PullRequestInfo]:
    return get_client().find_pr(branch)


def create_pr(branch: str, title: str, body: str, draft: bool = False) -> PullRequestInfo:
    return get_client().create_pr(branch, title, body, draft)
