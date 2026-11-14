"""OpsRelay orchestrator: drives an incident through its stages with crash recovery.

Guarantees
----------
* Dedup     - one incident per (repo, run_id, run_attempt), enforced by a unique index.
* Leases    - a worker must hold an incident's lease to advance it; every write is
              fenced on `lease.owner`, so a stale/zombie worker can't clobber state.
              A crashed worker's lease expires and another worker picks it up.
* Verified  - a stage is only marked done after its effect is verified (tests passed,
              PR found on GitHub by lookup - not just "the API call returned").
* Intent    - the PR branch/title is persisted BEFORE GitHub is called. On resume we
              look the PR up by that branch first, so a crash after GitHub created
              the PR never produces a duplicate.
* No replay - resume reads only the incident document's `stage`; the audit log in
              `workflow_steps` is for humans, not for recovery.
* Sources   - CI incidents come from Hamza's monitor; live incidents (source="live")
              come from Taha's uptime monitor via agents/intake.py. Both run the same stages.
* Human     - PRs are drafts and are never merged by the agent. `check_reviews` watches
  approval    them: merged -> status "resolved"; closed unmerged -> "rejected" and the
              fix's memory is deactivated so it is never suggested again.

Teammate contracts (duck-typed)
-------------------------------
Hamza  monitor: FailureMonitor.detect_failures() (wrapped by MonitorAdapter below)
       github: GitHubClient.find_pr + PullRequestPublisher (wrapped by GitHubAdapter below)
       github.open_draft_pr(repo, branch, base_sha, title, body, patch) -> {"number", "url"}
           (must create branch from base_sha if missing, commit patch["files"], open draft PR)
Faiz   repairer.repair(failed_run, similar_incidents) -> PatchResult

FailedRun   = {repo, app, run_id, run_attempt, commit_sha, failed_logs,
               failed_tests?: [str], html_url?: str, head_branch?: str}
PatchResult = {tests_passed: bool, test_output: str, diff: str,
               files: {path: new_content}, changed_files: [str],
               summary: str, root_cause?: str}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import time
import uuid
from pathlib import Path
from datetime import timedelta
from typing import Any, Protocol

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from agents import memory
from agents import mongodb as m

LEASE_SECONDS = int(os.getenv("LEASE_SECONDS", "120"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))

FAILED_RUN_FIELDS = ("repo", "app", "run_id", "run_attempt", "commit_sha",
                     "failed_logs", "failed_tests", "html_url", "head_branch", "source", "live")
PATCH_FIELDS = ("diff", "files", "changed_files", "summary", "root_cause", "added_tests", "investigation")


class GitHub(Protocol):
    def find_pr(self, repo: str, branch: str) -> dict | None: ...
    def open_draft_pr(self, repo: str, branch: str, base_sha: str, title: str,
                      body: str, patch: dict) -> dict: ...


class Repairer(Protocol):
    def repair(self, failed_run: dict, similar: list[dict]) -> dict: ...


class LeaseLost(Exception):
    """Another worker took over this incident; stop touching it."""


class StopRequested(Exception):
    pass


def incident_id_for(run: dict) -> str:
    return f"{run['repo']}#{run['run_id']}.{run['run_attempt']}"


def branch_for(inc: dict) -> str:
    """Deterministic repair branch - same incident always maps to the same branch.

    Uses Hamza's convention (agent/fix-<run_id>-<attempt>) so his monitor ignores
    CI runs on the agent's own fix branches. Live incidents use their uptime id
    (agent/fix-live-upt-novamart-20261114-171819).
    """
    if inc.get("source") == m.SOURCE_LIVE:
        slug = re.sub(r"[^a-z0-9-]+", "-", inc["live"]["uptime_incident_id"].lower()).strip("-")
        return f"agent/fix-live-{slug}"[:90]
    return f"agent/fix-{inc['run_id']}-{inc['run_attempt']}"


def crash_point(name: str) -> None:
    """Demo hook: OPSRELAY_CRASH_AT=pr_created kills the process right there."""
    if os.getenv("OPSRELAY_CRASH_AT") == name:
        print(f"\n💥 [crash] simulated crash at '{name}' (OPSRELAY_CRASH_AT)\n", flush=True)
        os._exit(42)


# --------------------------------------------------------------------------- #
# Ingest (Hamza -> Mo)                                                         #
# --------------------------------------------------------------------------- #
def ingest_failed_run(run: dict, db: Database | None = None,
                      worker_id: str = "monitor") -> tuple[str, bool]:
    """Record a failed run exactly once. Returns (incident_id, created)."""
    missing = [f for f in ("repo", "app", "run_id", "run_attempt", "commit_sha") if f not in run]
    if missing:
        raise ValueError(f"failed run missing fields: {missing}")
    iid = incident_id_for(run)
    now = m.utcnow()
    doc = {k: run[k] for k in FAILED_RUN_FIELDS if k in run}
    doc.update({
        "_id": iid, "status": m.STATUS_OPEN, "stage": "detected", "attempts": 0,
        "created_at": now, "updated_at": now,
        "stages": {"detected": {"at": now, "by": worker_id}},
    })
    try:
        res = m.incidents(db).update_one({"_id": iid}, {"$setOnInsert": doc}, upsert=True)
        created = res.upserted_id is not None
    except DuplicateKeyError:  # concurrent ingest of the same run
        created = False
    if created:
        m.log_step(iid, "detected", "completed", worker_id,
                   {"run_id": run["run_id"], "commit_sha": run["commit_sha"],
                    "html_url": run.get("html_url")}, db=db)
    return iid, created


def live_incident_id(repo: str, uptime_incident_id: str) -> str:
    return f"{repo}#{uptime_incident_id}"


def ingest_live_incident(repo: str, live: dict, commit_sha: str, db: Database | None = None,
                         worker_id: str = "uptime-intake") -> tuple[str, bool]:
    """Record a live incident from the uptime monitor exactly once. Returns (incident_id, created).

    ``live`` is the uptime incident in Taha's handoff shape (see agents/intake.py) and
    ``commit_sha`` the commit that was deployed when it was detected.
    """
    if not live.get("uptime_incident_id") or live.get("app") not in ("shopping", "messaging"):
        raise ValueError("live incident needs uptime_incident_id and app shopping|messaging")
    iid = live_incident_id(repo, live["uptime_incident_id"])
    now = m.utcnow()
    doc = {
        "_id": iid, "source": m.SOURCE_LIVE, "repo": repo, "app": live["app"], "commit_sha": commit_sha,
        "live": live, "status": m.STATUS_OPEN, "stage": "detected", "attempts": 0,
        "created_at": now, "updated_at": now, "stages": {"detected": {"at": now, "by": worker_id}},
    }
    res = m.incidents(db).update_one({"_id": iid}, {"$setOnInsert": doc}, upsert=True)
    created = res.upserted_id is not None
    if created:
        m.log_step(iid, "detected", "completed", worker_id,
                   {"uptime_incident_id": live["uptime_incident_id"], "commit_sha": commit_sha,
                    "route": live.get("investigation_route"), "title": live.get("title")}, db=db)
    return iid, created


def request_stop(incident_id: str, reason: str, by: str, db: Database | None = None) -> None:
    """Kill switch: the worker halts before its next stage and hands off to a human."""
    m.incidents(db).update_one({"_id": incident_id},
                               {"$set": {"stop_requested": {"reason": reason, "by": by,
                                                            "at": m.utcnow()}}})
    m.log_step(incident_id, "stop", "started", by, {"reason": reason}, db=db)


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #
class Orchestrator:
    def __init__(self, github: GitHub, repairer: Repairer, db: Database | None = None,
                 worker_id: str | None = None, lease_seconds: int = LEASE_SECONDS):
        self.github = github
        self.repairer = repairer
        self.db = m.get_db(db)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease_seconds = lease_seconds

    # ---- leases ----------------------------------------------------------- #
    def _lease(self) -> dict:
        return {"owner": self.worker_id,
                "expires_at": m.utcnow() + timedelta(seconds=self.lease_seconds)}

    def claim(self, incident_id: str) -> dict | None:
        """Take the lease if the incident is open and nobody live holds it."""
        now = m.utcnow()
        return m.incidents(self.db).find_one_and_update(
            {"_id": incident_id, "status": m.STATUS_OPEN,
             "$or": [{"lease": None}, {"lease.expires_at": {"$lt": now}},
                     {"lease.owner": self.worker_id}]},
            {"$set": {"lease": self._lease(), "updated_at": now}},
            return_document=True,
        )

    def _update(self, iid: str, set_: dict, unset: dict | None = None) -> dict:
        """Fenced write: only succeeds while we still own the lease. Renews it."""
        update: dict[str, Any] = {"$set": {**set_, "lease": self._lease(),
                                           "updated_at": m.utcnow()}}
        if unset:
            update["$unset"] = unset
        doc = m.incidents(self.db).find_one_and_update(
            {"_id": iid, "lease.owner": self.worker_id}, update, return_document=True)
        if doc is None:
            raise LeaseLost(iid)
        return doc

    def _advance(self, inc: dict, stage: str, fields: dict | None = None,
                 evidence: dict | None = None) -> dict:
        now = m.utcnow()
        doc = self._update(inc["_id"], {**(fields or {}), "stage": stage,
                                        f"stages.{stage}": {"at": now, "by": self.worker_id}})
        m.log_step(inc["_id"], stage, "completed", self.worker_id, evidence, db=self.db)
        print(f"  ✔ {stage}")
        return doc

    def _finish(self, inc: dict, status: str, reason: str | None = None) -> dict:
        doc = self._update(inc["_id"], {"status": status, "status_reason": reason})
        m.incidents(self.db).update_one({"_id": inc["_id"], "lease.owner": self.worker_id},
                                        {"$unset": {"lease": ""}})
        return doc

    # ---- main loop -------------------------------------------------------- #
    def run_incident(self, incident_id: str) -> dict | None:
        inc = self.claim(incident_id)
        if inc is None:
            return None  # done, or another live worker owns it
        resumed = inc["stage"] != "detected"
        print(f"▶ {incident_id} [{'RESUMING from ' + inc['stage'] if resumed else 'new'}]"
              f" worker={self.worker_id}")
        if resumed:
            m.log_step(incident_id, inc["stage"], "recovered", self.worker_id,
                       {"resumed_from": inc["stage"]}, db=self.db)
        handlers = {
            "memory_retrieved": self._retrieve_memory,
            "patch_verified": self._repair,
            "pr_intent_saved": self._save_pr_intent,
            "pr_confirmed": self._confirm_pr,
            "memory_saved": self._save_memory,
        }
        try:
            while True:
                idx = m.STAGES.index(inc["stage"])
                if idx == len(m.STAGES) - 1:
                    print(f"■ {incident_id} done -> PR {inc.get('pr', {}).get('url')}")
                    return self._finish(inc, m.STATUS_AWAITING_MERGE)
                if inc.get("stop_requested"):
                    raise StopRequested(inc["stop_requested"].get("reason"))
                nxt = m.STAGES[idx + 1]
                inc = handlers[nxt](inc)
                if inc["status"] != m.STATUS_OPEN:
                    return inc
        except LeaseLost:
            print(f"  ⚠ lost lease on {incident_id}; another worker took over")
            return None
        except StopRequested as e:
            m.log_step(incident_id, inc["stage"], "skipped", self.worker_id,
                       {"stopped": str(e)}, db=self.db)
            return self._finish(inc, m.STATUS_NEEDS_REVIEW, f"stopped: {e}")
        except Exception as e:  # noqa: BLE001 - record any stage failure, retry later
            return self._record_failure(inc, e)

    def _record_failure(self, inc: dict, err: Exception) -> dict | None:
        attempts = inc.get("attempts", 0) + 1
        nxt = m.STAGES[min(m.STAGES.index(inc["stage"]) + 1, len(m.STAGES) - 1)]
        m.log_step(inc["_id"], nxt, "failed", self.worker_id,
                   {"error": f"{type(err).__name__}: {err}", "attempt": attempts}, db=self.db)
        print(f"  ✘ {nxt} failed (attempt {attempts}/{MAX_ATTEMPTS}): {err}")
        try:
            if attempts >= MAX_ATTEMPTS:
                self._update(inc["_id"], {"attempts": attempts})
                return self._finish(inc, m.STATUS_FAILED, str(err))
            # Release the lease so this (or another) worker retries on the next pass.
            return m.incidents(self.db).find_one_and_update(
                {"_id": inc["_id"], "lease.owner": self.worker_id},
                {"$set": {"attempts": attempts, "last_error": str(err),
                          "updated_at": m.utcnow()}, "$unset": {"lease": ""}},
                return_document=True)
        except LeaseLost:
            return None

    # ---- stages ----------------------------------------------------------- #
    def _failed_run(self, inc: dict) -> dict:
        return {k: inc[k] for k in FAILED_RUN_FIELDS if k in inc}

    def _retrieve_memory(self, inc: dict) -> dict:
        similar = memory.find_similar(self._failed_run(inc), k=3, db=self.db)
        return self._advance(inc, "memory_retrieved", {"similar": similar}, {
            "matches": [{"memory_id": s["memory_id"], "score": round(s["score"], 3),
                         "summary": s.get("summary")} for s in similar]})

    def _repair(self, inc: dict) -> dict:
        result = self.repairer.repair(self._failed_run(inc), inc.get("similar", []))
        patch = {k: result.get(k) for k in PATCH_FIELDS if result.get(k) is not None}
        verification = {"tests_passed": bool(result.get("tests_passed")),
                        "test_output": (result.get("test_output") or "")[-4000:],
                        "verified_at": m.utcnow()}
        if not verification["tests_passed"]:
            # Don't open a PR for an unverified patch: leave it for a human.
            inc = self._update(inc["_id"], {"patch": patch, "verification": verification})
            m.log_step(inc["_id"], "patch_verified", "failed", self.worker_id,
                       {"tests_passed": False, "changed_files": patch.get("changed_files")},
                       db=self.db)
            print("  ✘ patch did not pass tests -> needs human review")
            reason = result.get("reason") or "patch failed verification"
            return self._finish(inc, m.STATUS_NEEDS_REVIEW, f"patch failed verification: {reason}"[:2000])
        return self._advance(inc, "patch_verified",
                             {"patch": patch, "verification": verification},
                             {"tests_passed": True, "changed_files": patch.get("changed_files"),
                              "summary": patch.get("summary")})

    def _save_pr_intent(self, inc: dict) -> dict:
        intent = {
            "branch": branch_for(inc),
            "base_sha": inc["commit_sha"],
            "title": (f"[OpsRelay] Fix {inc['app']} live incident: {inc['live'].get('title') or ''}"[:120]
                      if inc.get("source") == m.SOURCE_LIVE
                      else f"[OpsRelay] Fix {inc['app']} CI failure (run {inc['run_id']})"),
            "saved_at": m.utcnow(),
        }
        return self._advance(inc, "pr_intent_saved", {"pr_intent": intent},
                             {"branch": intent["branch"]})

    def _confirm_pr(self, inc: dict) -> dict:
        intent = inc["pr_intent"]
        existing = self.github.find_pr(inc["repo"], intent["branch"])
        if existing:
            # Recovery path: GitHub already has it (we crashed after creating it).
            print(f"  ↺ found existing PR #{existing['number']} for {intent['branch']}"
                  " - not creating a duplicate")
            return self._advance(inc, "pr_confirmed", {"pr": _pr_fields(existing)},
                                 {"pr_number": existing["number"], "url": existing["url"],
                                  "recovered_existing": True})
        self._update(inc["_id"], {"pr_intent.sent_at": m.utcnow()})
        m.log_step(inc["_id"], "pr_confirmed", "started", self.worker_id,
                   {"branch": intent["branch"]}, db=self.db)
        self.github.open_draft_pr(inc["repo"], intent["branch"], intent["base_sha"],
                                  intent["title"], _pr_body(inc), inc.get("patch", {}))
        crash_point("pr_created")
        # "Sent" isn't "done": confirm the PR actually exists before recording it.
        confirmed = self.github.find_pr(inc["repo"], intent["branch"])
        if not confirmed:
            raise RuntimeError(f"PR for {intent['branch']} not visible on GitHub after create")
        return self._advance(inc, "pr_confirmed", {"pr": _pr_fields(confirmed)},
                             {"pr_number": confirmed["number"], "url": confirmed["url"],
                              "recovered_existing": False})

    def _save_memory(self, inc: dict) -> dict:
        mem_id = memory.remember_fix(inc, db=self.db)
        return self._advance(inc, "memory_saved", {"memory_id": mem_id}, {"memory_id": mem_id})

    # ---- worker ----------------------------------------------------------- #
    def resumable_ids(self) -> list[str]:
        now = m.utcnow()
        cur = m.incidents(self.db).find(
            {"status": m.STATUS_OPEN,
             "$or": [{"lease": None}, {"lease.expires_at": {"$lt": now}},
                     {"lease.owner": self.worker_id}]},
            {"_id": 1}).sort("created_at", 1)
        return [d["_id"] for d in cur]

    def run_pending(self) -> int:
        ids = self.resumable_ids()
        for iid in ids:
            self.run_incident(iid)
        return len(ids)

    # ---- human approval ------------------------------------------------- #
    def check_reviews(self) -> list[tuple[str, str]]:
        """Record what humans decided on the agent's PRs. Returns [(incident_id, new_status)].

        Merged -> resolved (the fix's memory stays active). Closed without merge ->
        rejected and the memory is deactivated. Open PRs are left alone. Status moves are
        conditional on still being awaiting_merge, so concurrent workers record it once.
        """
        decided = []
        for inc in m.incidents(self.db).find({"status": m.STATUS_AWAITING_MERGE, "pr_intent.branch": {"$exists": True}}):
            pr = self.github.find_pr(inc["repo"], inc["pr_intent"]["branch"]) or {}
            state = pr.get("state")
            if state not in ("merged", "closed"):
                continue
            status = m.STATUS_RESOLVED if state == "merged" else m.STATUS_REJECTED
            now = m.utcnow()
            res = m.incidents(self.db).update_one(
                {"_id": inc["_id"], "status": m.STATUS_AWAITING_MERGE},
                {"$set": {"status": status, "review": {"pr_state": state, "recorded_at": now},
                          "status_reason": f"PR #{pr.get('number')} {state} by a human", "updated_at": now}})
            if not res.modified_count:
                continue
            if status == m.STATUS_REJECTED and inc.get("memory_id"):
                memory.forget(inc["memory_id"], f"PR #{pr.get('number')} closed without merge", db=self.db)
            m.log_step(inc["_id"], "review", "completed", self.worker_id,
                       {"pr_number": pr.get("number"), "pr_state": state, "status": status}, db=self.db)
            print(f"{'✔' if state == 'merged' else '✘'} {inc['_id']}: PR #{pr.get('number')} {state} -> {status}")
            decided.append((inc["_id"], status))
        return decided

    def run_forever(self, monitor, poll_seconds: int = POLL_SECONDS, live=None,
                    live_intake=None) -> None:
        """CI failures (monitor), live incidents from the uptime monitor (live_intake) and,
        if given, Mo's live checks (LiveWatcher), every poll_seconds."""
        print(f"OpsRelay worker {self.worker_id} starting; resuming incomplete incidents…")
        if live is not None:
            print(f"live checks every {poll_seconds}s: {', '.join(live.services)}")
        self.run_pending()
        while True:
            try:
                for run in monitor.poll_failed_runs():
                    iid, created = ingest_failed_run(run, db=self.db)
                    print(f"{'＋ new' if created else '· seen'} incident {iid}")
                if live_intake is not None:
                    for iid in live_intake.poll():
                        print(f"＋ new live incident {iid}")
                self.run_pending()
                self.check_reviews()
            except Exception as e:  # noqa: BLE001 - keep the worker alive
                print(f"[worker] loop error: {e}")
            if live is not None:
                try:
                    live.tick()
                except Exception as e:  # noqa: BLE001 - a live-check bug must not stop CI handling
                    print(f"[worker] live check error: {e}")
            time.sleep(poll_seconds)


def _pr_fields(pr: dict) -> dict:
    return {"number": pr["number"], "url": pr["url"], "confirmed_at": m.utcnow()}


def _pr_body(inc: dict) -> str:
    patch = inc.get("patch", {})
    similar = inc.get("similar", [])
    trigger = (f"live incident `{inc['live']['uptime_incident_id']}` ({inc['live'].get('title') or ''})"
               if inc.get("source") == m.SOURCE_LIVE
               else f"failed CI run {inc.get('html_url') or inc['run_id']}")
    lines = [
        f"Automated repair for {trigger} on `{inc['commit_sha'][:7]}`.",
        "", f"**Root cause:** {patch.get('root_cause') or 'n/a'}",
        f"**Fix:** {patch.get('summary') or 'n/a'}",
        f"**Files:** {', '.join(patch.get('changed_files') or [])}",
        "", "**Verification:** pytest passed on the patched code ✅", "",
    ]
    if similar:
        lines.append("**Similar past incidents (Atlas Vector Search):**")
        lines += [f"- ({s['score']:.2f}) {s.get('summary')}" for s in similar]
    lines += ["", f"OpsRelay incident `{inc['_id']}` — needs human review before merge."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
# Hamza's monitor reports the folder name; Faiz's apps.toml / our schema use the app key.
APP_KEYS = {"message-platform": "messaging", "shopping": "shopping", "messaging": "messaging"}


class MonitorAdapter:
    """Turns Hamza's FailureMonitor events + failure context into our FailedRun dicts."""

    def __init__(self, client, monitor):
        self.client = client
        self.monitor = monitor

    def poll_failed_runs(self) -> list[dict]:
        runs = []
        for ev in self.monitor.detect_failures():
            try:
                ctx = self.client.get_failure_context(ev.workflow_run_id, ev.run_attempt)
            except Exception:
                self.monitor.forget(ev.incident_id)  # re-report on the next poll
                raise
            runs.append({
                "repo": f"{self.client.config.owner}/{self.client.config.repo}",
                "app": APP_KEYS.get(ev.application, ev.application),
                "run_id": ev.workflow_run_id,
                "run_attempt": ev.run_attempt,
                "commit_sha": ev.commit_sha,
                "failed_logs": ctx.logs,
                "html_url": ev.workflow_url,
                "head_branch": ev.branch,
            })
        return runs


class PublishFailed(Exception):
    """GitHub definitively refused the patch (stale base, unsafe path…)."""


class GitHubAdapter:
    """Maps our find_pr / open_draft_pr calls onto Hamza's client + PullRequestPublisher.

    Hamza's publisher is itself idempotent (read-then-write, deterministic branch),
    so a retry after a crash never creates a second branch or PR.
    """

    def __init__(self, client, publisher):
        self.client = client
        self.publisher = publisher

    def find_pr(self, repo: str, branch: str) -> dict | None:
        pr = self.client.find_pr(branch, state="all")
        if pr is None:
            return None
        return {"number": pr.number, "url": pr.url, "state": pr.state}

    def open_draft_pr(self, repo: str, branch: str, base_sha: str, title: str,
                      body: str, patch: dict) -> dict:
        from agents.github_types import (APP_ROOTS, STATUS_FAILED, STATUS_VERIFIED,
                                         PublishRequest, fix_branch_name)

        incident_id = branch.removeprefix("agent/fix-")  # Hamza's id: "<run_id>-<attempt>"
        if fix_branch_name(incident_id) != branch:
            raise ValueError(f"branch {branch!r} doesn't follow agent/fix-<run>-<attempt>")
        paths = list((patch.get("files") or {}) if isinstance(patch.get("files"), dict)
                     else [f.get("path", "") for f in patch.get("files") or []])
        app = next((k for k, root in APP_ROOTS.items()
                    if any(p.startswith(root + "/") for p in paths)), "")
        # Only verified patches reach this stage (patch_verified gate).
        req = PublishRequest.from_dict({**patch, "tests_passed": True},
                                       incident_id=incident_id, base_sha=base_sha,
                                       application=app, title=title,
                                       context_notes=_similar_section(body))
        res = self.publisher.create_or_find_pr(req)
        if res.status == STATUS_FAILED:
            raise PublishFailed(res.detail)
        if res.status != STATUS_VERIFIED:  # "unknown": ambiguous, safe to retry
            raise RuntimeError(f"PR publish not confirmed: {res.detail}")
        return {"number": res.pr_number, "url": res.pr_url}


def _similar_section(body: str) -> str:
    """The 'Similar past incidents' block from the orchestrator's PR text, if any."""
    marker = "**Similar past incidents"
    start = (body or "").find(marker)
    if start < 0:
        return ""
    lines = []
    for line in body[start:].splitlines():
        if lines and not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def _build_real():
    """Wire up teammates' modules (Hamza: GitHub + monitor + publisher, Faiz: repair)."""
    from agents.github_client import GitHubClient
    from agents.git_sync import SyncingRepairer
    from agents.github_publisher import PullRequestPublisher
    from agents.monitor import FailureMonitor
    from agents.patcher import Repairer as FaizRepairer

    gh = GitHubClient()
    # `or`, not a getenv default: .env.example ships OPSRELAY_REPO_PATH= (blank)
    repo_path = os.getenv("OPSRELAY_REPO_PATH") or str(Path(__file__).resolve().parents[1])
    return (Orchestrator(GitHubAdapter(gh, PullRequestPublisher(gh)),
                         SyncingRepairer(FaizRepairer(repo_path=repo_path), repo_path)),
            MonitorAdapter(gh, FailureMonitor(gh)))


def _live_intake(orch: Orchestrator):
    from agents.intake import LiveIntake

    cfg = orch.github.client.config
    return LiveIntake(f"{cfg.owner}/{cfg.repo}", db=orch.db, base_branch=cfg.base_branch)


def _github_or_none():
    try:
        from agents.github_client import GitHubClient
        return GitHubClient()
    except Exception as e:  # noqa: BLE001 - CI status is optional evidence
        print(f"[live] GitHub unavailable, CI status will be 'unknown': {e}")
        return None


def _build_live(github, required: bool = False):
    from agents.live_incidents import LiveWatcher, live_services

    services = live_services()
    if not services:
        if required:
            raise SystemExit("Set OPSRELAY_LIVE_SERVICES (e.g. messaging) to choose services to watch.")
        return None
    # `or`, not a getenv default: .env.example ships OPSRELAY_REPO_PATH= (blank)
    repo_path = os.getenv("OPSRELAY_REPO_PATH") or str(Path(__file__).resolve().parents[1])
    return LiveWatcher(services, repo_path, github=github)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m agents.orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create collections, indexes, vector index")
    sub.add_parser("worker", help="resume incomplete incidents, then poll CI + live incidents forever "
                                  "(+ live checks if OPSRELAY_LIVE_SERVICES is set)")
    lv = sub.add_parser("live", help="live checks only: open/update incidents, collect evidence")
    lv.add_argument("--once", action="store_true", help="one round of checks, then exit")
    sub.add_parser("resume", help="resume incomplete incidents once and exit")
    sub.add_parser("intake", help="record new live incidents from the uptime monitor once and exit")
    sub.add_parser("reviews", help="record merged / closed agent PRs once and exit")
    s = sub.add_parser("status", help="show an incident and its audit timeline")
    s.add_argument("incident_id")
    st = sub.add_parser("stop", help="kill switch for an incident")
    st.add_argument("incident_id")
    st.add_argument("--reason", default="manual stop")
    sub.add_parser("list", help="list recent incidents")
    a = p.parse_args()

    if a.cmd == "setup":
        m.ensure_setup()
        print("setup complete")
    elif a.cmd == "worker":
        orch, monitor = _build_real()
        intake_on = os.getenv("OPSRELAY_LIVE_INTAKE", "1").lower() not in ("0", "false", "no")
        orch.run_forever(monitor, live=_build_live(orch.github.client),
                          live_intake=_live_intake(orch) if intake_on else None)
    elif a.cmd == "intake":
        orch, _ = _build_real()
        for iid in _live_intake(orch).poll():
            print(f"＋ new live incident {iid}")
    elif a.cmd == "reviews":
        orch, _ = _build_real()
        print(f"recorded {len(orch.check_reviews())} review decision(s)")
    elif a.cmd == "live":
        watcher = _build_live(_github_or_none(), required=True)
        while True:
            watcher.tick()
            if a.once:
                break
            time.sleep(POLL_SECONDS)
    elif a.cmd == "resume":
        orch, _ = _build_real()
        print(f"processed {orch.run_pending()} incident(s)")
    elif a.cmd == "status":
        inc = m.incidents().find_one({"_id": a.incident_id},
                                     {"failed_logs": 0, "similar": 0, "patch.files": 0})
        print(json.dumps(inc, indent=2, default=str))
        for step in m.incident_timeline(a.incident_id):
            print(f"{step['at']:%H:%M:%S}  {step['stage']:<17} {step['outcome']:<10} "
                  f"{step['worker_id']:<28} {json.dumps(step.get('evidence'), default=str)[:120]}")
    elif a.cmd == "stop":
        request_stop(a.incident_id, a.reason, by=os.getenv("USER", "operator"))
        print("stop requested")
    elif a.cmd == "list":
        for inc in m.incidents().find({}, {"status": 1, "stage": 1, "pr.url": 1,
                                          "created_at": 1}).sort("created_at", -1).limit(20):
            print(f"{inc['_id']:<50} {inc['status']:<15} {inc['stage']:<17} "
                  f"{(inc.get('pr') or {}).get('url', '')}")


if __name__ == "__main__":
    main()
