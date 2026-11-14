"""Typed contract between the repair module and Mo's orchestrator.

Everything here is a Pydantic model so the orchestrator can persist results
with ``model_dump()`` without this module touching MongoDB or GitHub.
"""

from __future__ import annotations

import tomllib
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


class Evidence(BaseModel):
    source: Literal["ci_log", "recent_diff", "source_file", "similar_incident"]
    reference: str = Field(description="File path, incident id, or 'ci_log'.")
    excerpt: str = Field(description="Verbatim excerpt from the supplied input.")
    supports: str = Field(description="How this excerpt supports the diagnosis.")


class Diagnosis(BaseModel):
    outcome: Literal["diagnosed", "needs_review"]
    likely_cause: str
    explanation: str
    evidence: list[Evidence]
    suspect_files: list[str]
    failing_tests: list[str] = Field(description="Pytest node ids parsed from the CI log.")
    confidence: float
    review_reason: str | None = None


class PatchResult(BaseModel):
    accepted: bool
    diff: str | None = None
    changed_files: list[str] = Field(default_factory=list)
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


class RepairResult(BaseModel):
    outcome: Outcome
    reason: str
    app: str
    commit_sha: str
    diagnosis: Diagnosis | None = None
    patch: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    baseline: TestRun | None = Field(default=None, description="Failing tests on original code.")
    reproduce_after_patch: TestRun | None = None
    patched_suite: TestRun | None = Field(default=None, description="Full app suite after patch.")
