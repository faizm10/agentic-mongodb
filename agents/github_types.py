"""Shared data contracts for the GitHub integration layer (Developer 1).

These dataclasses are the "language" the other developers speak to us:

* Developer 2 (AI agent) receives a ``FailureContext`` and returns a verified patch
  (``RepairResult`` / the ``Repairer`` dict), which we turn into a ``PublishRequest``.
* Developer 3 (MongoDB / workflow) receives ``FailureEvent`` objects and stores the
  ``GitHubOperationResult`` that we return after publishing a PR.

``to_dict()`` returns camelCase keys (``incidentId``, ``workflowRunId`` ...) for the
failure-side objects. ``GitHubOperationResult.to_dict()`` is the exception: it returns
snake_case keys because that is the shape agreed with Developer 3
(``status``, ``pr_number``, ``pr_url``, ``branch``, ``commit_sha``).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Constants shared by monitor + publisher
# --------------------------------------------------------------------------- #

# Branches created by the agent always start with this prefix. The monitor ignores
# anything on such a branch so the agent never reacts to its own pull requests.
AGENT_BRANCH_PREFIX = "agent/"

# Workflow file name -> app key. The app keys match the keys in agents/apps.toml
# ("shopping", "messaging"). Edit here if Developer 4 renames a workflow.
WORKFLOW_FILE_TO_APP: dict[str, str] = {
    "shopping-ci.yml": "shopping",
    "messaging-ci.yml": "messaging",
}

# App key -> folder in the monorepo.
APP_ROOTS: dict[str, str] = {
    "shopping": "shopping",
    "messaging": "message-platform",
}

# Possible values of GitHubOperationResult.status
STATUS_VERIFIED = "verified"  # we re-read GitHub and confirmed the result
STATUS_FAILED = "failed"      # definitive: GitHub rejected it, or the patch cannot be published
STATUS_UNKNOWN = "unknown"    # ambiguous (timeout / 5xx / rate limit). Safe to call again.


def make_incident_id(run_id: int, run_attempt: int) -> str:
    """Unique, deterministic incident id, e.g. ``123456789-1``."""
    return f"{run_id}-{run_attempt}"


def fix_branch_name(incident_id: str) -> str:
    """Deterministic fix-branch name, e.g. ``agent/fix-123456789-1``."""
    return f"{AGENT_BRANCH_PREFIX}fix-{incident_id}"


def application_for_workflow_path(path: Optional[str]) -> Optional[str]:
    """Map a workflow path (``.github/workflows/shopping-ci.yml``) to an app key.

    Returns ``None`` for workflows we do not care about.
    """
    if not path:
        return None
    path = path.split("@", 1)[0]  # runs sometimes report "path@refs/heads/main"
    filename = path.rsplit("/", 1)[-1]
    return WORKFLOW_FILE_TO_APP.get(filename)


def app_root(application: str) -> str:
    """Folder of an app in the repo (``messaging`` -> ``message-platform``)."""
    return APP_ROOTS.get(application, application)


# --------------------------------------------------------------------------- #
# Serialisation helper
# --------------------------------------------------------------------------- #

def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def to_camel_dict(obj: Any) -> Any:
    """Recursively convert dataclasses to dicts with camelCase keys."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {_camel(f.name): to_camel_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_camel_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_camel_dict(v) for k, v in obj.items()}
    return obj


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return to_camel_dict(self)


# --------------------------------------------------------------------------- #
# Monitor output
# --------------------------------------------------------------------------- #

@dataclass
class FailureEvent(_Serializable):
    """A failed CI run that the monitor detected. Cheap: no logs fetched yet."""

    incident_id: str            # "<run_id>-<attempt>"
    application: str            # app key: "shopping" | "messaging"
    workflow_run_id: int
    run_attempt: int
    commit_sha: str
    workflow_name: str
    workflow_file: str          # e.g. "shopping-ci.yml"
    branch: str
    workflow_url: str
    status: str = "failure"
    created_at: Optional[str] = None


# --------------------------------------------------------------------------- #
# Failure context (Developer 1 -> Developer 2)
# --------------------------------------------------------------------------- #

@dataclass
class FailedJob(_Serializable):
    job_id: int
    name: str
    conclusion: str
    url: str
    failed_steps: list[str] = field(default_factory=list)


@dataclass
class ChangedFile(_Serializable):
    """A file touched by the failing commit (with its diff when GitHub provides one)."""

    path: str
    status: str                 # added | modified | removed | renamed ...
    additions: int = 0
    deletions: int = 0
    patch: Optional[str] = None


@dataclass
class RepoFile(_Serializable):
    """File contents at a given ref. ``sha`` is the git *blob* sha."""

    path: str
    ref: str
    sha: str
    content: str
    truncated: bool = False


@dataclass
class CommitInfo(_Serializable):
    sha: str
    message: str
    author: str
    url: str
    files: list[ChangedFile] = field(default_factory=list)


_FAILED_TEST_RE = re.compile(r"^FAILED\s+(\S+::\S+)", re.M)


@dataclass
class FailureContext(_Serializable):
    """Everything Developer 2 needs to diagnose an incident."""

    incident_id: str
    application: str
    workflow_run_id: int
    run_attempt: int
    commit_sha: str
    failed_job: str
    logs: str
    workflow_url: str
    failed_job_id: Optional[int] = None
    failed_jobs: list[str] = field(default_factory=list)
    commit_message: str = ""
    branch: str = ""
    changed_files: list[ChangedFile] = field(default_factory=list)
    source_files: list[RepoFile] = field(default_factory=list)

    def to_failed_run(self, repo_full_name: str) -> dict[str, Any]:
        """The ``FailedRun`` dict Mo's orchestrator uses.

        Shape taken from agents/tests/test_orchestrator_adapter.py - confirm with Mo.
        """
        tests = list(dict.fromkeys(_FAILED_TEST_RE.findall(self.logs)))
        return {
            "repo": repo_full_name,
            "app": self.application,
            "run_id": self.workflow_run_id,
            "run_attempt": self.run_attempt,
            "commit_sha": self.commit_sha,
            "failed_logs": self.logs,
            "failed_tests": tests,
        }


# --------------------------------------------------------------------------- #
# Publishing contracts (Phase 2)
# --------------------------------------------------------------------------- #

@dataclass
class PatchFile(_Serializable):
    path: str
    content: str                # COMPLETE corrected file contents


@dataclass
class PublishRequest(_Serializable):
    """A verified patch that we publish as a branch + pull request.

    Build it from Developer 2's output with ``from_dict`` (the ``Repairer`` dict) or
    ``from_repair_result`` (a ``RepairResult``).
    """

    incident_id: str
    base_sha: str               # commit the patch was generated against
    files: list[PatchFile]
    root_cause: str
    tests_passed: bool
    application: str = ""
    summary: str = ""           # one-line description of the fix
    test_summary: str = ""      # tail of the passing test output
    diff: str = ""
    workflow_url: str = ""

    @classmethod
    def from_dict(
        cls,
        d: dict[str, Any],
        *,
        incident_id: Optional[str] = None,
        base_sha: Optional[str] = None,
        application: str = "",
        workflow_url: str = "",
    ) -> "PublishRequest":
        """Accepts the ``Repairer.repair()`` dict (files as ``{path: content}``) or a
        list of ``{"path", "content"}`` items. ``tests_passed`` falls back to
        ``outcome == "verified"``.

        The Repairer dict does not carry the commit the patch was made against, so pass
        ``incident_id`` and ``base_sha`` (the failed run's commit) as arguments unless the
        dict already has ``incident_id`` / ``base_sha`` / ``commit_sha``."""
        incident = incident_id or d.get("incident_id")
        base_sha = base_sha or d.get("base_sha") or d.get("commit_sha")
        if not incident or not base_sha:
            raise ValueError("patch needs an incident id and a base sha (base_sha or commit_sha)")
        raw_files = d.get("files") or {}
        if isinstance(raw_files, dict):
            files = [PatchFile(path=p, content=c) for p, c in raw_files.items()]
        else:
            files = [PatchFile(path=f["path"], content=f["content"]) for f in raw_files]
        tests_passed = d["tests_passed"] if "tests_passed" in d else d.get("outcome") == "verified"
        return cls(
            incident_id=incident,
            base_sha=base_sha,
            files=files,
            root_cause=d.get("root_cause", ""),
            tests_passed=bool(tests_passed),
            application=application or d.get("app", "") or d.get("application", ""),
            summary=d.get("summary") or d.get("proposed_fix", ""),
            test_summary=d.get("test_summary") or d.get("test_output", ""),
            diff=d.get("diff") or "",
            workflow_url=workflow_url or d.get("workflow_url", ""),
        )

    @classmethod
    def from_repair_result(
        cls, incident_id: str, result: Any, *, application: str = "", workflow_url: str = ""
    ) -> "PublishRequest":
        """From Developer 2's ``RepairResult`` (pydantic model or its ``model_dump()``)."""
        data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        diagnosis = data.get("diagnosis") or {}
        suite = data.get("patched_suite") or {}
        return cls(
            incident_id=incident_id,
            base_sha=data["commit_sha"],
            files=[PatchFile(path=p, content=c) for p, c in (data.get("patched_files") or {}).items()],
            root_cause=diagnosis.get("likely_cause") or data.get("reason", ""),
            tests_passed=data.get("outcome") == "verified",
            application=application or data.get("app", ""),
            summary=data.get("reason", ""),
            test_summary=suite.get("output_tail", ""),
            diff=data.get("patch") or "",
            workflow_url=workflow_url,
        )


@dataclass
class BranchInfo(_Serializable):
    name: str
    sha: str


@dataclass
class PullRequestInfo(_Serializable):
    number: int
    url: str
    state: str                  # "open" | "closed" | "merged"
    head_ref: str
    head_sha: str
    base_ref: str
    title: str = ""


@dataclass
class GitHubOperationResult(_Serializable):
    """What we hand to Developer 3 after touching GitHub.

    ``to_dict()`` is snake_case on purpose (the agreed contract with Developer 3).
    """

    status: str                 # STATUS_VERIFIED | STATUS_FAILED | STATUS_UNKNOWN
    branch: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_state: Optional[str] = None      # "open" | "closed" | "merged"
    detail: str = ""
    steps: list[str] = field(default_factory=list)  # what was done / skipped, for logs + demo

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
