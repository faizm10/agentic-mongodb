"""Shared data contracts for the GitHub integration layer (Developer 1).

These dataclasses are the "language" the other developers speak to us:

* Developer 2 (AI agent) receives a ``FailureContext`` and returns a ``PatchResult``.
* Developer 3 (MongoDB / workflow) receives ``FailureEvent`` objects and stores the
  ``GitHubOperationResult`` that we return after publishing a PR.

Every dataclass has ``to_dict()`` which returns camelCase keys (``incidentId``,
``workflowRunId`` ...) so it can be JSON-serialised / stored in MongoDB directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Constants shared by monitor + publisher
# --------------------------------------------------------------------------- #

# Branches created by the agent always start with this prefix. The monitor ignores
# anything on such a branch so the agent never reacts to its own pull requests.
AGENT_BRANCH_PREFIX = "agent/"

# Workflow file name -> application folder. Edit here if Developer 4 renames a workflow.
WORKFLOW_FILE_TO_APP: dict[str, str] = {
    "shopping-ci.yml": "shopping",
    "messaging-ci.yml": "message-platform",
}

# Possible values of GitHubOperationResult.status
STATUS_VERIFIED = "verified"  # we re-read GitHub and confirmed the result
STATUS_FAILED = "failed"      # GitHub definitively rejected the operation
STATUS_UNKNOWN = "unknown"    # ambiguous (timeout / 5xx): outcome could not be confirmed


def make_incident_id(run_id: int, run_attempt: int) -> str:
    """Unique, deterministic incident id, e.g. ``123456789-1``."""
    return f"{run_id}-{run_attempt}"


def fix_branch_name(incident_id: str) -> str:
    """Deterministic fix-branch name, e.g. ``agent/fix-123456789-1``."""
    return f"{AGENT_BRANCH_PREFIX}fix-{incident_id}"


def application_for_workflow_path(path: Optional[str]) -> Optional[str]:
    """Map a workflow path (``.github/workflows/shopping-ci.yml``) to an app name.

    Returns ``None`` for workflows we do not care about.
    """
    if not path:
        return None
    path = path.split("@", 1)[0]  # runs sometimes report "path@refs/heads/main"
    filename = path.rsplit("/", 1)[-1]
    return WORKFLOW_FILE_TO_APP.get(filename)


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
    application: str            # "shopping" | "message-platform"
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
    """File contents at a given ref. ``sha`` is the git *blob* sha (needed for updates)."""

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


# --------------------------------------------------------------------------- #
# Patch publishing contracts (Phase 2 will use these)
# --------------------------------------------------------------------------- #

@dataclass
class PatchFile(_Serializable):
    path: str
    content: str                # COMPLETE corrected file contents


@dataclass
class PatchResult(_Serializable):
    """What Developer 2 hands to us. In Phase 2 we publish it as a branch + PR."""

    incident_id: str
    base_sha: str
    files: list[PatchFile]
    root_cause: str
    tests_passed: bool
    proposed_fix: str = ""
    test_summary: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PatchResult":
        return cls(
            incident_id=d["incident_id"],
            base_sha=d["base_sha"],
            files=[PatchFile(path=f["path"], content=f["content"]) for f in d["files"]],
            root_cause=d.get("root_cause", ""),
            tests_passed=bool(d.get("tests_passed", False)),
            proposed_fix=d.get("proposed_fix", ""),
            test_summary=d.get("test_summary", ""),
        )


@dataclass
class GitHubOperationResult(_Serializable):
    """What we hand to Developer 3 after touching GitHub (Phase 2)."""

    status: str                 # STATUS_VERIFIED | STATUS_FAILED | STATUS_UNKNOWN
    branch: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    commit_sha: Optional[str] = None
    detail: str = ""
