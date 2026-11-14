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

Teammate contracts (duck-typed)
-------------------------------
Hamza  monitor.poll_failed_runs() -> list[FailedRun]
       github.find_pr(repo, branch) -> {"number", "url", ...} | None
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
import socket
import time
import uuid
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
                     "failed_logs", "failed_tests", "html_url", "head_branch")


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
    """Deterministic repair branch - same incident always maps to the same branch."""
    return f"opsrelay/fix-{inc['app']}-{inc['run_id']}-{inc['run_attempt']}"


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
        patch = {k: result.get(k) for k in ("diff", "files", "changed_files", "summary",
                                            "root_cause") if result.get(k) is not None}
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
            return self._finish(inc, m.STATUS_NEEDS_REVIEW, "patch failed verification")
        return self._advance(inc, "patch_verified",
                             {"patch": patch, "verification": verification},
                             {"tests_passed": True, "changed_files": patch.get("changed_files"),
                              "summary": patch.get("summary")})

    def _save_pr_intent(self, inc: dict) -> dict:
        intent = {
            "branch": branch_for(inc),
            "base_sha": inc["commit_sha"],
            "title": f"[OpsRelay] Fix {inc['app']} CI failure (run {inc['run_id']})",
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

    def run_forever(self, monitor, poll_seconds: int = POLL_SECONDS) -> None:
        print(f"OpsRelay worker {self.worker_id} starting; resuming incomplete incidents…")
        self.run_pending()
        while True:
            try:
                for run in monitor.poll_failed_runs():
                    iid, created = ingest_failed_run(run, db=self.db)
                    print(f"{'＋ new' if created else '· seen'} incident {iid}")
                self.run_pending()
            except Exception as e:  # noqa: BLE001 - keep the worker alive
                print(f"[worker] loop error: {e}")
            time.sleep(poll_seconds)


def _pr_fields(pr: dict) -> dict:
    return {"number": pr["number"], "url": pr["url"], "confirmed_at": m.utcnow()}


def _pr_body(inc: dict) -> str:
    patch = inc.get("patch", {})
    similar = inc.get("similar", [])
    lines = [
        f"Automated repair for failed CI run {inc.get('html_url') or inc['run_id']} "
        f"on `{inc['commit_sha'][:7]}`.",
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
def _build_real():
    """Wire up teammates' modules. Adjust names here once their code lands."""
    from agents.github_client import GitHubClient  # Hamza
    from agents.monitor import Monitor  # Hamza
    from agents.patcher import Repairer as FaizRepairer  # Faiz

    gh = GitHubClient()
    return Orchestrator(gh, FaizRepairer()), Monitor(gh)


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m agents.orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="create collections, indexes, vector index")
    sub.add_parser("worker", help="resume incomplete incidents, then poll forever")
    sub.add_parser("resume", help="resume incomplete incidents once and exit")
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
        orch.run_forever(monitor)
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
