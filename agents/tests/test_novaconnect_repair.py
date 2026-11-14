"""Repair against Taha's real NovaConnect backend (message-platform/backend), using agents/apps.toml.

Mirrors test_novamart_repair.py: builds a throwaway git repo from the committed
message-platform/ tree, commits the ``duplicate`` bug from
message-platform/inject_bug.py, runs the suite as CI would, then hands that log to
the ``Repairer`` entry point. The model is a stub; the app code, its tests, the
config and the verifier are all real.
"""

import importlib.util
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from agents import Repairer
from agents.investigator import _EvidenceDraft

from .support import _git, candidate, diagnosis_draft, patch_draft, stub_llm

REPO_ROOT = Path(__file__).resolve().parents[2]
MESSAGES = "message-platform/backend/app/messages.py"


def _load_bugs():
    spec = importlib.util.spec_from_file_location(
        "novaconnect_inject_bug", REPO_ROOT / "message-platform" / "inject_bug.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module.BUGS


@pytest.fixture(scope="module")
def novaconnect(tmp_path_factory):
    tree = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "HEAD", "message-platform/backend"],
                          capture_output=True)
    if tree.returncode != 0 or not tree.stdout:
        pytest.skip("message-platform/backend is not committed in this checkout")
    repo = tmp_path_factory.mktemp("novaconnect")
    with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
        tar.extractall(repo, filter="data")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "novaconnect")

    bug = _load_bugs()["duplicate"]
    path = repo / "message-platform" / bug.file
    path.write_text(path.read_text().replace(bug.original, bug.buggy, 1))
    _git(repo, "commit", "-q", "-am", "messages: simplify idempotency check")
    sha = _git(repo, "rev-parse", "HEAD")

    ci = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
                        cwd=repo / "message-platform" / "backend", capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert ci.returncode == 1, ci.stdout[-2000:]
    return repo, sha, bug, ci.stdout


def test_real_messaging_config_is_used(monkeypatch):
    monkeypatch.delenv("OPSRELAY_APPS_CONFIG", raising=False)
    from agents import load_app_config

    app = load_app_config("messaging")
    assert (app.root, app.source_dirs) == ("message-platform/backend",
                                           ["message-platform/backend/app"])
    assert (REPO_ROOT / app.root / "pytest.ini").is_file()
    assert (REPO_ROOT / MESSAGES).is_file()


def test_injected_duplicate_bug_is_repaired_and_verified(novaconnect, monkeypatch):
    monkeypatch.delenv("OPSRELAY_APPS_CONFIG", raising=False)
    repo, sha, bug, ci_log = novaconnect
    buggy_line = bug.buggy.splitlines()[0].strip()
    diagnosis = diagnosis_draft(
        likely_cause="The idempotency guard is inverted, so retries with a key are stored twice.",
        explanation="The last commit changed `if not idempotency_key` to `if idempotency_key` in messages.py.",
        evidence=[
            _EvidenceDraft(source="recent_diff", reference="recent_diff",
                           excerpt="+" + bug.buggy.splitlines()[0],
                           supports="The last commit inverted the idempotency check."),
            _EvidenceDraft(source="source_file", reference=MESSAGES, excerpt=buggy_line,
                           supports="The failed commit still has the inverted check."),
        ],
        suspect_files=[MESSAGES],
    )
    fix = patch_draft(candidate(path=MESSAGES, find=bug.buggy, replace=bug.original,
                                summary="Restore the idempotency guard: skip lookup only when no key."))
    failed_run = {"repo": "faizm10/agentic-mongodb", "app": "messaging", "run_id": 2, "run_attempt": 1,
                  "commit_sha": sha, "failed_logs": ci_log}

    result = Repairer(repo_path=str(repo), llm=stub_llm(diagnosis, fix)).repair(failed_run, [])

    assert result["outcome"] == "verified", result["reason"]
    assert result["changed_files"] == [MESSAGES]
    assert bug.original in result["files"][MESSAGES]
    assert " passed" in result["test_output"] and "failed" not in result["test_output"]
