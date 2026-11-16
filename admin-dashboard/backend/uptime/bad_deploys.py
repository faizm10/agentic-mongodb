"""Demo bad deploys: ship a real bug to the base branch so the repair agent has code to fix.

The bugs come from each app's ``inject_bug.py`` catalog. Shipping one commits that
single-line change on top of ``origin/<branch>`` and pushes it, like a bad deploy, then
applies the same change to the running checkout (the dev servers reload on save). The
uptime probe catches it, and the agent fixes the pushed commit and opens a PR.

A ``provider:`` scenario is a third-party outage instead (the payment provider is down):
it flips the app's demo outage flag and ships no code, so the agent has nothing to fix.

A cross-service scenario ships a regression *and* starts a controlled fault in another
app. ``checkout-confirmation`` makes NovaMart report a failed checkout when the order
confirmation to NovaConnect times out, then makes NovaConnect's confirmations time out.
Its regression is defined here rather than in an app's ``inject_bug.py``, because the
agent reads those catalogs (agents/live_repair.py) and must find this one from evidence.
Applying the merged fix (``restore(..., keep_faults=True)``) leaves NovaConnect's fault
running: ending it is a separate operational step.

Restore applies the reverse change to the checkout and, if the bug is still on the
branch (the agent's PR was not merged yet), pushes a revert commit.

Commits are built with git plumbing in a temporary index, so the operator's current
branch, index and uncommitted files are never touched. Which bug is live is recorded in
``.dev/`` at the repository root, outside the app trees the agent reads.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from . import demo_outages
from .demo_outages import repo_root

APPS = {"novamart": "shopping", "novaconnect": "message-platform"}
# The dashboard sends a scenario number, so nothing it shows or sends names the bug.
SCENARIOS = {
    "novamart": ("images", "provider:payment", "stock", "checkout-confirmation"),
    "novaconnect": ("send", "receive", "both"),
}
CROSS_SERVICE = {
    "checkout-confirmation": SimpleNamespace(
        file="backend/app/checkout.py",
        original="            return CheckoutResult(order, attempt, replayed)  # the order is saved; recovery resends "
                 "the confirmation\n",
        buggy="            raise ConfirmationUnavailableError(exc.message, details={\"order_id\": order[\"order_id\"], "
              "**exc.to_dict()}) from exc\n",
        fault=("novaconnect", "confirmation_timeout"),
    ),
}
# Bland, like a routine cleanup: neither the agent nor an audience should learn where
# the bug is from the commit message.
COMMIT_MESSAGES = {
    "quantity": "Tidy up checkout code",
    "discount": "Clean up storefront backend",
    "inventory": "Refactor cart helpers",
    "duplicate": "Tidy up messaging backend",
    "status": "Clean up conversation code",
    "ordering": "Refactor message helpers",
    "images": "Tidy up catalog constants",
    "stock": "Simplify availability lookup",
    "send": "Tidy up message sending",
    "receive": "Clean up history query",
    "both": "Refactor participant lookup",
    "checkout-confirmation": "Simplify checkout error handling",
}
GIT_TIMEOUT = 60
_lock = threading.Lock()


class BadDeployError(RuntimeError):
    """The bad deploy cannot be shipped or restored; the message is safe to show."""


def remote() -> str:
    return os.environ.get("OPSRELAY_DEPLOY_REMOTE") or "origin"


def branch() -> str:
    return os.environ.get("OPSRELAY_DEPLOY_BRANCH") or os.environ.get("GITHUB_BASE_BRANCH") or "main"


def state_path() -> Path:
    return repo_root() / ".dev" / "bad-deploys.json"


def catalog(service_id: str) -> dict[str, Any]:
    """The app's bug catalog (``inject_bug.BUGS``), keyed by bug name."""
    app = APPS.get(service_id)
    if app is None:
        raise ValueError("Unknown demo service")
    path = repo_root() / app / "inject_bug.py"
    if not path.is_file():
        raise FileNotFoundError(f"{app}/inject_bug.py is not present in this checkout")
    spec = importlib.util.spec_from_file_location(f"opsrelay_inject_bug_{service_id}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module.BUGS


def _bug(service_id: str, bug_key: str):
    return CROSS_SERVICE.get(bug_key) or catalog(service_id)[bug_key]


def ship(service_id: str, scenario: int) -> None:
    """Ship scenario 1-N of this app's ``SCENARIOS``."""
    with _lock:
        keys = SCENARIOS.get(service_id, ())
        if not 1 <= scenario <= len(keys):
            raise ValueError("Unsupported bad deploy")
        bug_key = keys[scenario - 1]
        state = _load_state()
        if service_id in state:
            raise BadDeployError("A bad deploy is already live for this app. Restore it first.")
        if bug_key.startswith("provider:"):
            outage = bug_key.removeprefix("provider:")
            demo_outages.activate(service_id, outage)
            state[service_id] = {"outage": outage}
            _save_state(state)
            return
        bug = _bug(service_id, bug_key)
        path = f"{APPS[service_id]}/{bug.file}"
        local = repo_root() / path
        text = local.read_text(encoding="utf-8")
        if text.count(bug.original) != 1:
            raise BadDeployError("This app's code in the checkout has local changes. Restore it first.")

        base = _fetch()
        commit = _commit_swap(base, path, bug.original, bug.buggy, COMMIT_MESSAGES[bug_key])
        _push(commit)
        _write(local, text.replace(bug.original, bug.buggy, 1))
        state[service_id] = {"bug": bug_key, "commit": commit, "branch": branch()}
        fault = getattr(bug, "fault", None)
        if fault:
            demo_outages.activate(*fault)
            state[service_id]["fault"] = list(fault)
        _save_state(state)


def restore(service_id: str, *, keep_faults: bool = False) -> None:
    """Undo the live bad deploy for this app, if any. Safe to repeat.

    ``keep_faults`` applies only the code fix and leaves a cross-service fault running.
    """
    with _lock:
        if service_id not in APPS:
            raise ValueError("Unknown demo service")
        state = _load_state()
        entry = state.get(service_id)
        if entry is None:
            return
        if "outage" in entry:
            demo_outages.clear(service_id)
            del state[service_id]
            _save_state(state)
            return
        bug = _bug(service_id, entry["bug"])
        if entry.get("fault") and not keep_faults:
            demo_outages.clear(entry["fault"][0])
        path = f"{APPS[service_id]}/{bug.file}"
        local = repo_root() / path
        text = local.read_text(encoding="utf-8")
        if text.count(bug.buggy) == 1:
            _write(local, text.replace(bug.buggy, bug.original, 1))

        base = _fetch()
        if _show(base, path).count(bug.buggy) == 1:  # still on the branch: the fix PR is not merged
            _push(_commit_swap(base, path, bug.buggy, bug.original, f'Revert "{COMMIT_MESSAGES[entry["bug"]]}"'))
        del state[service_id]
        _save_state(state)


# ----------------------------------------------------------------------------- git
def _git(*args: str, input: str | None = None, env: dict[str, str] | None = None) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(repo_root()), *args], input=input, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BadDeployError(f"git {args[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        raise BadDeployError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _fetch() -> str:
    _git("fetch", "--quiet", remote(), branch())
    return _git("rev-parse", "--verify", "FETCH_HEAD^{commit}").strip()


def _show(commit: str, path: str) -> str:
    return _git("show", f"{commit}:{path}")


def _commit_swap(base: str, path: str, old: str, new: str, message: str) -> str:
    """A commit on top of ``base`` that changes ``old`` to ``new`` in ``path``, built without a checkout."""
    content = _show(base, path)
    if content.count(old) != 1:
        raise BadDeployError(f"{remote()}/{branch()} no longer has the code this scenario expects.")
    mode = _git("ls-tree", base, "--", path).split()[0]
    blob = _git("hash-object", "-w", "--stdin", input=content.replace(old, new, 1)).strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git("read-tree", base, env=env)
        _git("update-index", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        tree = _git("write-tree", env=env).strip()
    return _git("commit-tree", tree, "-p", base, "-m", message).strip()


def _push(commit: str) -> None:
    _git("push", "--quiet", remote(), f"{commit}:refs/heads/{branch()}")


# ------------------------------------------------------------------------ files
def _write(path: Path, text: str) -> None:
    fd, temp = tempfile.mkstemp(prefix=".opsrelay-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if path.exists():
            os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    path = state_path()
    if not state:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, json.dumps(state, indent=2))
