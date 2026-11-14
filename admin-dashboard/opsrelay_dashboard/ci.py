"""Latest GitHub Actions runs per workflow. Read only; cached to stay under rate limits."""

from __future__ import annotations

import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")

Fetch = Callable[[str, dict[str, str]], tuple[int, bytes]]


def _tls_context() -> ssl.SSLContext:
    # Some Python builds (e.g. uv-managed on macOS) ship without a usable system CA
    # store; use certifi's bundle when it is installed.
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _urlopen(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10, context=_tls_context()) as response:  # noqa: S310 - fixed api.github.com URL
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class GitHubActions:
    def __init__(
        self,
        repo: str,
        token: str | None = None,
        ttl_seconds: float = 60.0,
        fetch: Fetch = _urlopen,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not _REPO_RE.match(repo):
            raise ValueError(f"invalid GitHub repo {repo!r}")
        self.repo = repo
        self._token = token
        self._ttl = ttl_seconds
        self._fetch = fetch
        self._clock = clock
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def latest(self, workflow: str | None, limit: int = 5) -> dict:
        if not workflow:
            return {"state": "unconfigured", "runs": [], "error": "no workflow configured"}
        if not _WORKFLOW_RE.match(workflow):
            return {"state": "unknown", "runs": [], "error": f"invalid workflow file name {workflow!r}"}
        with self._lock:
            cached = self._cache.get(workflow)
            if cached and self._clock() - cached[0] < self._ttl:
                return cached[1]
        result = self._load(workflow, limit)
        with self._lock:
            self._cache[workflow] = (self._clock(), result)
        return result

    def _load(self, workflow: str, limit: int) -> dict:
        url = f"https://api.github.com/repos/{self.repo}/actions/workflows/{workflow}/runs?per_page={limit}"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "opsrelay-dashboard/1"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            status, body = self._fetch(url, headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"state": "unknown", "runs": [], "error": f"GitHub unreachable: {exc}"[:200]}
        if status == 404:
            return {"state": "unconfigured", "runs": [], "error": f"workflow {workflow} not found in {self.repo}"}
        if status != 200:
            return {"state": "unknown", "runs": [], "error": f"GitHub API returned HTTP {status}"}
        try:
            runs = json.loads(body).get("workflow_runs", [])
        except ValueError:
            return {"state": "unknown", "runs": [], "error": "GitHub API returned invalid JSON"}

        simplified = [
            {
                "id": run.get("id"),
                "title": run.get("display_title") or run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "branch": run.get("head_branch"),
                "sha": (run.get("head_sha") or "")[:7],
                "created_at": run.get("created_at"),
                "url": run.get("html_url"),
            }
            for run in runs[:limit]
        ]
        return {"state": _state(simplified), "runs": simplified, "error": None}


def _state(runs: list[dict]) -> str:
    if not runs:
        return "no_runs"
    latest = runs[0]
    if latest["status"] != "completed":
        return "running"
    return {"success": "passing", "failure": "failing", "timed_out": "failing"}.get(latest["conclusion"], "unknown")
