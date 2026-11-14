"""Bad deploys push a real catalog bug to the remote branch and restore it, never naming it."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from uptime.api import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
FILES = ["shopping/inject_bug.py", "shopping/backend/app/pricing.py", "shopping/backend/app/inventory.py",
         "message-platform/inject_bug.py", "message-platform/backend/app/messages.py",
         "message-platform/backend/app/delivery.py"]
PRICING = "shopping/backend/app/pricing.py"
CLEAN = "        discount = subtotal_cents * promotion.value // 100\n"
BUGGY = "        discount = subtotal_cents * promotion.value // 10\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def remote_file(origin: Path, path: str) -> str:
    return subprocess.run(["git", "-C", str(origin), "show", f"main:{path}"], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A clone of a bare 'GitHub' remote holding the real bug catalogs and app sources."""
    for key, value in {"GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.test",
                       "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.test"}.items():
        monkeypatch.setenv(key, value)
    for key in ("OPSRELAY_DEPLOY_REMOTE", "OPSRELAY_DEPLOY_BRANCH", "GITHUB_BASE_BRANCH"):
        monkeypatch.delenv(key, raising=False)
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    for rel in FILES:
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / rel, seed / rel)
    git(seed.parent, "init", "-q", "-b", "main", str(seed))
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "checkout", "-q", "-b", "feature")  # the operator is on another branch
    monkeypatch.setenv("OPSRELAY_DEMO_REPO_ROOT", str(work))
    return work, origin


@pytest.fixture
def client(tmp_path, settings, clock):
    from .conftest import make_sqlite

    return TestClient(create_app(make_sqlite(tmp_path), settings, clock=clock))


def test_ship_pushes_the_bug_and_restore_reverts_it(checkout, client):
    work, origin = checkout
    base = git(origin, "rev-parse", "main")
    (work / "notes.txt").write_text("uncommitted operator work")

    response = client.post("/api/v1/demo/bad-deploys/novamart", json={"scenario": 2})
    assert response.status_code == 200, response.text
    assert "discount" not in response.text and "pricing" not in response.text

    # The remote gained exactly one commit on top of the old head, changing one line.
    assert git(origin, "rev-parse", "main~1") == base
    assert git(origin, "log", "-1", "--format=%s", "main") == "Clean up storefront backend"
    assert git(origin, "diff", "--name-only", base, "main") == PRICING
    assert BUGGY in remote_file(origin, PRICING)
    # The running checkout serves the bug; the operator's branch, index and files are untouched.
    assert BUGGY in (work / PRICING).read_text()
    assert git(work, "branch", "--show-current") == "feature"
    assert git(work, "diff", "--cached", "--name-only") == ""
    assert (work / "notes.txt").read_text() == "uncommitted operator work"
    assert (work / ".dev" / "bad-deploys.json").exists()  # outside the app trees the agent reads

    second = client.post("/api/v1/demo/bad-deploys/novamart", json={"scenario": 1})
    assert second.status_code == 409

    assert client.delete("/api/v1/demo/bad-deploys/novamart").status_code == 200
    assert git(origin, "log", "-1", "--format=%s", "main") == 'Revert "Clean up storefront backend"'
    assert remote_file(origin, PRICING) == (REPO_ROOT / PRICING).read_text()
    assert CLEAN in (work / PRICING).read_text()
    assert not (work / ".dev" / "bad-deploys.json").exists()
    assert client.delete("/api/v1/demo/bad-deploys/novamart").status_code == 200  # nothing left to restore


def test_restore_after_the_fix_was_merged_only_cleans_the_checkout(checkout, client, tmp_path):
    work, origin = checkout
    assert client.post("/api/v1/demo/bad-deploys/novamart", json={"scenario": 2}).status_code == 200

    # The agent's fix PR is merged on the remote.
    fixer = tmp_path / "fixer"
    subprocess.run(["git", "clone", "-q", str(origin), str(fixer)], check=True)
    path = fixer / PRICING
    path.write_text(path.read_text().replace(BUGGY, CLEAN))
    git(fixer, "commit", "-q", "-am", "fix discount")
    git(fixer, "push", "-q", "origin", "main")
    merged = git(origin, "rev-parse", "main")

    assert client.delete("/api/v1/demo/bad-deploys/novamart").status_code == 200
    assert git(origin, "rev-parse", "main") == merged  # no revert pushed
    assert CLEAN in (work / PRICING).read_text()


def test_novaconnect_bug_and_bad_requests(checkout, client):
    work, origin = checkout
    assert client.post("/api/v1/demo/bad-deploys/novaconnect", json={"scenario": 4}).status_code == 422
    assert client.post("/api/v1/demo/bad-deploys/unknown", json={"scenario": 2}).status_code == 404
    assert client.post("/api/v1/demo/bad-deploys/novaconnect", json={"scenario": 2}).status_code == 200
    assert "return bool(online_user_ids)" in remote_file(origin, "message-platform/backend/app/delivery.py")
    assert client.delete("/api/v1/demo/bad-deploys/novaconnect").status_code == 200


def test_ship_refuses_when_the_checkout_already_differs(checkout, client):
    work, origin = checkout
    base = git(origin, "rev-parse", "main")
    path = work / PRICING
    path.write_text(path.read_text().replace(CLEAN, BUGGY))  # hand-edited, not via the dashboard

    response = client.post("/api/v1/demo/bad-deploys/novamart", json={"scenario": 2})
    assert response.status_code == 409
    assert git(origin, "rev-parse", "main") == base  # nothing pushed
