"""Repair against Taha's real NovaMart backend (shopping/backend), using agents/apps.toml.

Builds a throwaway git repo from the committed shopping/ tree, commits the
``discount`` bug from shopping/inject_bug.py, runs the suite there as CI would,
then hands that log to Mo's ``Repairer`` entry point. The model is a stub; the
app code, its 85 tests, the config and the verifier are all real.
"""

import importlib.util
import io
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from agents import Repairer
from agents.contracts import APPS_CONFIG_PATH
from agents.investigator import _EvidenceDraft

from .support import _git, candidate, diagnosis_draft, patch_draft, stub_llm

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICING = "shopping/backend/app/pricing.py"


def _load_bugs():
    spec = importlib.util.spec_from_file_location("novamart_inject_bug", REPO_ROOT / "shopping" / "inject_bug.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module.BUGS


@pytest.fixture(scope="module")
def novamart(tmp_path_factory):
    tree = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", "HEAD", "shopping/backend"],
                          capture_output=True)
    if tree.returncode != 0 or not tree.stdout:
        pytest.skip("shopping/backend is not committed in this checkout")
    repo = tmp_path_factory.mktemp("novamart")
    with tarfile.open(fileobj=io.BytesIO(tree.stdout)) as tar:
        tar.extractall(repo, filter="data")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "novamart")

    bug = _load_bugs()["discount"]
    path = repo / "shopping" / bug.file
    path.write_text(path.read_text().replace(bug.original, bug.buggy, 1))
    _git(repo, "commit", "-q", "-am", "pricing: tweak promo math")
    sha = _git(repo, "rev-parse", "HEAD")

    ci = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider"],
                        cwd=repo / "shopping" / "backend", capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    assert ci.returncode == 1, ci.stdout[-2000:]
    return repo, sha, bug, ci.stdout


def test_real_app_config_is_used(monkeypatch):
    monkeypatch.delenv("OPSRELAY_APPS_CONFIG")
    from agents import load_app_config

    app = load_app_config("shopping")
    assert APPS_CONFIG_PATH.name == "apps.toml"
    assert (app.root, app.source_dirs) == ("shopping/backend", ["shopping/backend/app"])
    assert (REPO_ROOT / app.root / "pytest.ini").is_file()


def test_injected_discount_bug_is_repaired_and_verified(novamart, monkeypatch):
    monkeypatch.delenv("OPSRELAY_APPS_CONFIG")
    repo, sha, bug, ci_log = novamart
    buggy = bug.buggy.strip()
    diagnosis = diagnosis_draft(
        likely_cause="Percentage promotions divide by 10 instead of 100, giving 10x the discount.",
        explanation="The last commit changed the percentage discount divisor in pricing.py from 100 to 10.",
        evidence=[
            _EvidenceDraft(source="recent_diff", reference="recent_diff", excerpt="+" + bug.buggy.rstrip("\n"),
                           supports="The last commit introduced the // 10 divisor."),
            _EvidenceDraft(source="source_file", reference=PRICING, excerpt=buggy,
                           supports="The failed commit still divides by 10."),
        ],
        suspect_files=[PRICING],
    )
    fix = patch_draft(candidate(path=PRICING, find=bug.buggy, replace=bug.original,
                                summary="Divide percentage promotions by 100."))
    failed_run = {"repo": "faizm10/agentic-mongodb", "app": "shopping", "run_id": 1, "run_attempt": 1,
                  "commit_sha": sha, "failed_logs": ci_log}

    result = Repairer(repo_path=str(repo), llm=stub_llm(diagnosis, fix)).repair(failed_run, [])

    assert result["outcome"] == "verified", result["reason"]
    assert result["changed_files"] == [PRICING]
    assert "-" + bug.buggy.rstrip("\n") in result["diff"]
    assert "+" + bug.original.rstrip("\n") in result["diff"]
    assert bug.original in result["files"][PRICING]
    assert "85 passed" in result["test_output"]
    print(f"\nCI log tail:\n{ci_log.strip().splitlines()[-1]}\npatch:\n{result['diff']}\n{result['test_output']}")
