"""Typed contract between the repair module and Mo's orchestrator.

Everything here is a Pydantic model so the orchestrator can persist results
with ``model_dump()`` without this module touching MongoDB or GitHub.
"""

from __future__ import annotations

import tomllib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

APPS_CONFIG_PATH = Path(__file__).with_name("apps.toml")

Outcome = Literal["verified", "needs_review"]


# --- App configuration ------------------------------------------------------


class AppConfig(BaseModel):
    """Per-app repair scope and fixed test commands, loaded from apps.toml."""

    name: str
    root: str = Field(description="App directory relative to the repo root.")
    source_dirs: list[str] = Field(description="Repo-relative dirs a patch may edit.")
    test_dir: str = Field(description="Test dir relative to the app root.")
    pytest_args: list[str] = Field(description="Fixed args placed after `python -m pytest`.")
    timeout_seconds: int = 120
    max_changed_lines: int = 60
    max_changed_files: int = 3
    baseline_runs: int = Field(2, description="Times the failing tests must fail before patching.")
    max_candidates: int = Field(3, description="Alternative patches requested per round.")
    max_rounds: int = Field(2, description="Patch rounds; later rounds see earlier failures.")


def load_app_config(app: str, path: Path = APPS_CONFIG_PATH) -> AppConfig:
    with path.open("rb") as f:
        apps = tomllib.load(f)["apps"]
    if app not in apps:
        raise KeyError(f"unknown app {app!r}; configured apps: {sorted(apps)}")
    return AppConfig(name=app, **apps[app])


# --- Inputs -----------------------------------------------------------------


class PriorIncident(BaseModel):
    """A similar past incident from Mo's incident memory. Treated as a hint only."""

    id: str
    summary: str
    root_cause: str | None = None
    fix_summary: str | None = None
    similarity: float | None = None


class RepairRequest(BaseModel):
    app: str = Field(description="Key in apps.toml, e.g. 'shopping'.")
    repo_path: str = Field(description="Local git repo that contains commit_sha. Read only.")
    commit_sha: str
    failed_test_logs: str
    recent_diff: str = ""
    source_files: dict[str, str] = Field(
        default_factory=dict, description="Repo-relative path -> content at commit_sha."
    )
    similar_incidents: list[PriorIncident] = Field(default_factory=list)


# --- Outputs ----------------------------------------------------------------


EvidenceSource = Literal["ci_log", "reproduction", "recent_diff", "source_file", "similar_incident"]


class Evidence(BaseModel):
    source: EvidenceSource
    reference: str = Field(description="File path, incident id, 'ci_log' or 'reproduction'.")
    excerpt: str = Field(description="Verbatim excerpt from the supplied input.")
    supports: str = Field(description="How this excerpt supports the diagnosis.")


class TracebackFrame(BaseModel):
    path: str = Field(description="Repo-relative path.")
    line: int
    function: str | None = None
    in_source: bool = Field(description="Inside the app's editable source dirs.")


class Diagnosis(BaseModel):
    outcome: Literal["diagnosed", "needs_review"]
    likely_cause: str
    explanation: str
    evidence: list[Evidence]
    suspect_files: list[str] = Field(description="Ranked: traceback frames first, deepest first.")
    failing_tests: list[str] = Field(description="Pytest node ids parsed from the CI log.")
    traceback_frames: list[TracebackFrame] = Field(default_factory=list)
    confidence: float
    review_reason: str | None = None


class PatchResult(BaseModel):
    accepted: bool
    diff: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    changed_lines: int = 0
    summary: str = ""
    rejection_reason: str | None = None


class TestRun(BaseModel):
    __test__ = False  # not a pytest test class

    command: list[str]
    returncode: int | None
    passed: bool
    timed_out: bool = False
    duration_seconds: float
    output_tail: str


class CandidateAttempt(BaseModel):
    """One proposed patch and what happened to it."""

    round: int
    index: int
    summary: str
    patch: str | None
    changed_files: list[str]
    changed_lines: int
    passed: bool
    reason: str
    reproduce_runs: list[TestRun] = Field(default_factory=list)
    suite: TestRun | None = None


class RepairResult(BaseModel):
    outcome: Outcome
    reason: str
    app: str
    commit_sha: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    flaky: bool = Field(False, description="Failing tests passed on at least one baseline rerun.")
    diagnosis: Diagnosis | None = None
    patch: str | None = Field(None, description="The chosen verified patch.")
    changed_files: list[str] = Field(default_factory=list)
    baseline: list[TestRun] = Field(default_factory=list, description="Failing tests on original code.")
    reproduce_after_patch: list[TestRun] = Field(default_factory=list)
    patched_suite: TestRun | None = Field(default=None, description="Full app suite after patch.")
    attempts: list[CandidateAttempt] = Field(default_factory=list)
