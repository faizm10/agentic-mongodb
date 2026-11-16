"""Reconcile the checkouts a NovaMart incident interrupted, safely across worker crashes.

When the uptime monitor sees a fresh checkout pass again it reports the service as
Operational but keeps the incident open in its ``recovering`` phase
(admin-dashboard/backend/uptime/incidents.py). This worker then settles every checkout
attempt from the incident window by reading the records in the services that own them:

  order saved, confirmation delivered        -> completed
  order saved, confirmation confirmed absent -> resend only the confirmation (stable ID), then
                                                read it back from NovaConnect
  order saved, confirmation only accepted    -> wait: an acknowledgement is not a delivery
  order confirmed absent                     -> failed; the customer can retry
  a lookup failed or the records disagree    -> needs review, listing the missing evidence

Durable state lives in Atlas (``checkout_recoveries``, one document per uptime incident):
the incident ID, the affected checkout IDs, each attempt's retry intent (saved *before* the
resend) and its verified result. Writes are fenced on a lease, as in the orchestrator, so
a worker that lost its lease cannot overwrite its successor. A worker that dies during a
resend leaves the intent behind. The next worker looks the confirmation up by its stable ID
before it would ever send again, and NovaConnect stores one confirmation per ID, so recovery
never produces a second confirmation.

The incident closes (monitor side) once the phase here is ``complete``: every affected
attempt has a verified final outcome that NovaMart has recorded too.

CLI:
    python -m agents.recovery worker            # poll forever
    python -m agents.recovery once              # one pass over recovering incidents
    python -m agents.recovery status <incident> # attempts, workers and confirmation counts
    python -m agents.recovery arm-crash         # one-shot demo fault, see crash_once()
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
from pymongo import ReturnDocument
from pymongo.database import Database

from agents import mongodb as m

LEASE_SECONDS = int(os.getenv("OPSRELAY_RECOVERY_LEASE_SECONDS", "20"))
POLL_SECONDS = float(os.getenv("OPSRELAY_RECOVERY_POLL_SECONDS", "5"))
LOOKUP_TIMEOUT = float(os.getenv("OPSRELAY_RECOVERY_LOOKUP_TIMEOUT", "3"))
# Checkouts this long before the first failed check belong to the incident too: the monitor
# needs at least one 30 s cycle (more with a failure threshold) to notice.
LOOKBACK = timedelta(minutes=int(os.getenv("OPSRELAY_RECOVERY_LOOKBACK_MINUTES", "5")))
CRASH_POINT = "after_confirmation_recorded"

PENDING = "pending"                    # not settled yet (or a resend will be retried)
AWAITING_DELIVERY = "awaiting_delivery"
NEEDS_REVIEW = "needs_review"
COMPLETED = "completed"
FAILED = "failed"
FINAL = frozenset({COMPLETED, FAILED})


class LookupFailed(Exception):
    """A service did not answer, so its record could not be read (or the resend not sent)."""

    def __init__(self, source: str, kind: str, message: str) -> None:
        super().__init__(message)
        self.source, self.kind, self.message = source, kind, message


class LeaseLost(Exception):
    pass


# --------------------------------------------------------------------------- #
# Service records over HTTP                                                    #
# --------------------------------------------------------------------------- #
class _Http:
    source = "service"

    def __init__(self, base_url: str, timeout: float = LOOKUP_TIMEOUT,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _request(self, method: str, path: str, *, ok_404: bool = False, **kwargs) -> dict | None:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.TimeoutException as exc:
            raise LookupFailed(self.source, "timeout",
                               f"{self.source} did not answer {method} {path} within {self.timeout:.1f}s") from exc
        except httpx.HTTPError as exc:
            raise LookupFailed(self.source, "connection_error",
                               f"{self.source} {method} {path} failed: {type(exc).__name__}") from exc
        if ok_404 and response.status_code == 404:
            return None
        if not response.is_success:
            code = ((_json(response) or {}).get("error") or {}).get("code")
            raise LookupFailed(self.source, "http_error",
                               f"{self.source} {method} {path} answered HTTP {response.status_code}"
                               + (f" ({code})" if code else ""))
        return _json(response)


def _json(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class NovaMartRecords(_Http):
    """NovaMart owns checkout attempts and orders."""

    source = "NovaMart"

    def __init__(self, base_url: str | None = None, **kwargs) -> None:
        super().__init__(base_url or os.getenv("OPSRELAY_SHOPPING_URL") or "http://127.0.0.1:8100", **kwargs)

    def unfinished_checkouts(self, since: datetime, until: datetime) -> list[dict]:
        body = self._request("GET", "/api/checkouts", params={"since": since.isoformat(), "until": until.isoformat(),
                                                              "unfinished": "true", "limit": 500})
        return list((body or {}).get("checkouts") or [])

    def checkout(self, checkout_id: str) -> dict:
        return self._request("GET", f"/api/checkouts/{checkout_id}") or {}

    def resend_confirmation(self, checkout_id: str) -> dict:
        return self._request("POST", f"/api/checkouts/{checkout_id}/confirmation") or {}

    def record_outcome(self, checkout_id: str, outcome: str, incident_id: str, evidence: dict) -> None:
        self._request("PUT", f"/api/checkouts/{checkout_id}/reconciliation",
                      json={"incident_id": incident_id, "outcome": outcome, "evidence": evidence})


class NovaConnectRecords(_Http):
    """NovaConnect owns confirmations."""

    source = "NovaConnect"

    def __init__(self, base_url: str | None = None, **kwargs) -> None:
        super().__init__(base_url or os.getenv("OPSRELAY_MESSAGING_URL") or "http://127.0.0.1:8200", **kwargs)

    def confirmation(self, confirmation_id: str) -> dict | None:
        """The stored confirmation, or None when NovaConnect confirms it has none."""
        return self._request("GET", f"/api/confirmations/{confirmation_id}", ok_404=True)

    def confirmations_for_order(self, order_id: str) -> list[dict]:
        body = self._request("GET", "/api/confirmations", params={"order_id": order_id})
        return list((body or {}).get("confirmations") or [])


# --------------------------------------------------------------------------- #
# One-shot demo fault                                                          #
# --------------------------------------------------------------------------- #
def fault_file() -> Path:
    default = Path(__file__).resolve().parents[1] / ".dev" / "recovery-fault.json"
    return Path(os.getenv("OPSRELAY_RECOVERY_FAULT_FILE") or default)


def arm_crash(point: str = CRASH_POINT) -> Path:
    path = fault_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"crash_at": point, "armed_at": m.utcnow().isoformat()}), encoding="utf-8")
    return path


def _consume_fault(point: str) -> bool:
    """True exactly once per armed fault: the file is renamed away before the crash."""
    path = fault_file()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("crash_at") != point:
            return False
        os.replace(path, path.with_suffix(".fired"))
    except (OSError, ValueError):
        return False
    return True


def crash_once(point: str) -> None:
    if _consume_fault(point):
        print(f"\n💥 [recovery] demo fault: exiting at '{point}' before the result is saved\n", flush=True)
        os._exit(43)


# --------------------------------------------------------------------------- #
# Worker                                                                      #
# --------------------------------------------------------------------------- #
class RecoveryWorker:
    def __init__(self, novamart: NovaMartRecords | None = None, novaconnect: NovaConnectRecords | None = None,
                 db: Database | None = None, worker_id: str | None = None, lease_seconds: int = LEASE_SECONDS,
                 clock: Callable[[], datetime] = m.utcnow, crash: Callable[[str], None] = crash_once) -> None:
        self.novamart = novamart or NovaMartRecords()
        self.novaconnect = novaconnect or NovaConnectRecords()
        self.db = m.get_db(db)
        self.coll = m.checkout_recoveries(self.db)
        self.worker_id = worker_id or f"recovery:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.crash = crash

    # ---- discovery -------------------------------------------------------- #
    def recovering_incidents(self) -> list[dict]:
        return list(self.db[m.UPTIME_INCIDENTS].find({"state": "open", "recovery_phase": "recovering"})
                    .sort("started_at", 1))

    def poll(self) -> list[dict]:
        """One pass: reconcile every incident that is recovering. Returns the documents worked on."""
        done = []
        for incident in self.recovering_incidents():
            try:
                doc = self.run(incident)
            except LeaseLost:
                continue
            if doc is not None:
                done.append(doc)
        return done

    # ---- lease and fenced writes ----------------------------------------- #
    def _lease(self) -> dict:
        return {"owner": self.worker_id, "expires_at": self.clock() + timedelta(seconds=self.lease_seconds)}

    def _ensure(self, incident: dict) -> None:
        now = self.clock()
        recovered_at = incident.get("service_recovered_at") or now
        self.coll.update_one({"_id": incident["_id"]}, {"$setOnInsert": {
            "_id": incident["_id"], "incident_id": incident["_id"], "service_id": incident.get("service_id"),
            "app": incident.get("app"), "phase": "collecting",
            "window": {"since": incident["started_at"] - LOOKBACK, "until": recovered_at},
            "fresh_checkout": {"check_id": incident.get("recovery_check_id"), "at": recovered_at},
            "affected_checkout_ids": [], "attempts": {}, "workers": [], "events": [],
            "created_at": now, "updated_at": now,
        }}, upsert=True)

    def claim(self, incident_id: str) -> dict | None:
        now = self.clock()
        doc = self.coll.find_one_and_update(
            {"_id": incident_id, "phase": {"$ne": "complete"},
             "$or": [{"lease": None}, {"lease.expires_at": {"$lt": now}}, {"lease.owner": self.worker_id}]},
            {"$set": {"lease": self._lease(), "updated_at": now}, "$addToSet": {"workers": self.worker_id}},
            return_document=ReturnDocument.AFTER)
        if doc is not None and not any(e.get("worker_id") == self.worker_id and e["type"] == "worker_claimed"
                                       for e in doc.get("events", [])):
            resumed = [cid for cid, a in doc["attempts"].items() if a.get("retry_intent") and not a.get("final")]
            doc = self._update(incident_id, events=[self._event(
                "worker_claimed",
                f"Worker {self.worker_id} took over"
                + (f"; resuming interrupted resend(s) for {', '.join(resumed)}" if resumed else ""))])
        return doc

    def _update(self, incident_id: str, set_: dict | None = None, events: list[dict] | None = None,
                unset: dict | None = None) -> dict:
        update: dict[str, Any] = {"$set": {**(set_ or {}), "lease": self._lease(), "updated_at": self.clock()}}
        if events:
            update["$push"] = {"events": {"$each": events}}
        if unset:
            update["$unset"] = unset
        doc = self.coll.find_one_and_update({"_id": incident_id, "lease.owner": self.worker_id}, update,
                                            return_document=ReturnDocument.AFTER)
        if doc is None:
            raise LeaseLost(incident_id)
        return doc

    def _event(self, type_: str, message: str, checkout_id: str | None = None) -> dict:
        return {"at": self.clock(), "worker_id": self.worker_id, "type": type_, "message": message,
                **({"checkout_id": checkout_id} if checkout_id else {})}

    def _save_attempt(self, doc: dict, attempt: dict, event: tuple[str, str] | None = None) -> dict:
        events = [self._event(event[0], event[1], attempt["checkout_id"])] if event else None
        return self._update(doc["_id"], {f"attempts.{attempt['checkout_id']}": attempt}, events)

    # ---- one incident ---------------------------------------------------- #
    def run(self, incident: dict) -> dict | None:
        self._ensure(incident)
        doc = self.claim(incident["_id"])
        if doc is None:
            return None  # complete, or another live worker holds the lease
        if doc["phase"] == "collecting":
            doc = self._collect(doc)
            if doc["phase"] == "collecting":
                return self._release(doc)
        for checkout_id in doc["affected_checkout_ids"]:
            attempt = doc["attempts"][checkout_id]
            if attempt.get("final") and attempt.get("outcome_recorded"):
                continue
            doc = self._reconcile(doc, dict(attempt)) if not attempt.get("final") else self._record_outcome(doc, dict(attempt))
        return self._finish(doc)

    def _collect(self, doc: dict) -> dict:
        window = doc["window"]
        try:
            found = self.novamart.unfinished_checkouts(window["since"], window["until"])
        except LookupFailed as exc:
            return self._update(doc["_id"], events=[self._event(
                "collect_failed", f"Could not list interrupted checkouts: {exc.message}. Will retry.")])
        attempts = {}
        for a in found:
            cid = a["checkout_id"]
            attempts[cid] = {
                "checkout_id": cid, "order_id": a.get("order_id"),
                "confirmation_id": a.get("confirmation_id") or f"cnf-{cid}",
                "state_at_discovery": a.get("state"), "customer_saw": a.get("response"),
                "request_ids": a.get("request_ids") or [], "outcome": PENDING, "final": False,
                "outcome_recorded": False, "evidence": {}, "missing": [], "retry_intent": None,
                "verified_result": None, "reason": "Not reconciled yet.",
            }
        ids = [a["checkout_id"] for a in sorted(found, key=lambda a: a["started_at"])]
        return self._update(doc["_id"], {"phase": "reconciling", "attempts": attempts, "affected_checkout_ids": ids},
                            [self._event("collected", f"{len(ids)} interrupted checkout(s) to reconcile: "
                                                      f"{', '.join(ids) or 'none'}")])

    def _reconcile(self, doc: dict, a: dict) -> dict:
        cid = a["checkout_id"]
        now = self.clock()
        try:
            record = self.novamart.checkout(cid)
        except LookupFailed as exc:
            a["evidence"]["order"] = {"state": "lookup_failed", "error": exc.message, "checked_at": now}
            return self._review(doc, a, [f"NovaMart order record for checkout {cid} ({exc.kind})"],
                                f"The order lookup failed: {exc.message}")
        order = record.get("order")
        attempt_record = record.get("attempt") or {}
        a["order_id"] = (order or {}).get("order_id") or a.get("order_id")
        a["customer_saw"] = attempt_record.get("response") or a.get("customer_saw")
        a["evidence"]["order"] = {"state": record.get("order_state"), "order_id": a["order_id"], "checked_at": now}
        confirmation_id = (order or {}).get("confirmation_id") or a["confirmation_id"]
        a["confirmation_id"] = confirmation_id

        try:
            confirmation = self.novaconnect.confirmation(confirmation_id)
        except LookupFailed as exc:
            a["evidence"]["confirmation"] = {"state": "lookup_failed", "error": exc.message, "checked_at": now}
            return self._review(doc, a, [f"NovaConnect confirmation record {confirmation_id} ({exc.kind})"],
                                f"The confirmation lookup failed: {exc.message}")
        a["evidence"]["confirmation"] = _describe(confirmation, now)

        if order is None:
            if confirmation is not None:
                return self._review(doc, a, [], f"Records conflict: NovaConnect has confirmation {confirmation_id} "
                                                f"for order {confirmation.get('order_id')}, but NovaMart has no order "
                                                f"for checkout {cid}.")
            return self._final(doc, a, FAILED, "No order was saved for this checkout. The customer was not charged "
                                               "and can retry.")
        if confirmation is not None and (confirmation.get("order_id"), confirmation.get("checkout_id")) != (
                order["order_id"], cid):
            return self._review(doc, a, [], f"Records conflict: confirmation {confirmation_id} names order "
                                            f"{confirmation.get('order_id')}, NovaMart's order is {order['order_id']}.")
        if confirmation is not None:
            return self._settle_found(doc, a, confirmation, resumed=bool(a.get("retry_intent")))

        # Confirmation confirmed missing: resend only the confirmation. Save the intent first.
        if not a.get("retry_intent"):
            a["retry_intent"] = {"confirmation_id": confirmation_id, "at": now, "worker_id": self.worker_id}
            doc = self._save_attempt(doc, a, ("retry_intent_saved",
                                              f"Confirmation {confirmation_id} is missing; resending it."))
        try:
            self.novamart.resend_confirmation(cid)
        except LookupFailed as exc:
            a.update(outcome=PENDING, final=False,
                     reason=f"Resending the confirmation failed ({exc.message}). It will be retried.")
            return self._save_attempt(doc, a, ("resend_failed", a["reason"]))
        self.crash(CRASH_POINT)
        try:
            confirmation = self.novaconnect.confirmation(confirmation_id)
        except LookupFailed as exc:
            a["evidence"]["confirmation"] = {"state": "lookup_failed", "error": exc.message, "checked_at": self.clock()}
            return self._review(doc, a, [f"NovaConnect read-back of {confirmation_id} ({exc.kind})"],
                                "The confirmation was resent but could not be read back.")
        a["evidence"]["confirmation"] = _describe(confirmation, self.clock())
        if confirmation is None:
            return self._review(doc, a, [f"NovaConnect record of {confirmation_id}"],
                                "NovaMart reported the resend, but NovaConnect has no record of it.")
        return self._settle_found(doc, a, confirmation, resumed=False, resent=True)

    def _settle_found(self, doc: dict, a: dict, confirmation: dict, *, resumed: bool, resent: bool = False) -> dict:
        if confirmation.get("status") != "delivered":
            a.update(outcome=AWAITING_DELIVERY, final=False, missing=[],
                     reason="NovaConnect accepted the confirmation but has not delivered it yet.")
            return self._save_attempt(doc, a, ("awaiting_delivery", a["reason"]))
        try:
            count = len(self.novaconnect.confirmations_for_order(a["order_id"]))
        except LookupFailed:
            count = None
        a["verified_result"] = {
            "confirmation_id": confirmation["confirmation_id"], "status": "delivered",
            "delivered_at": confirmation.get("delivered_at"), "read_back_at": self.clock(),
            "confirmations_for_order": count, "resent": resent, "found_after_restart": resumed,
        }
        if resent:
            reason = "Confirmation was missing; resent once under its stable ID and read back as delivered."
        elif resumed:
            reason = ("Found the confirmation by its stable ID after an interrupted resend; it was not sent again. "
                      "Read back as delivered.")
        else:
            reason = "Order saved and its confirmation was already delivered; nothing was resent."
        return self._final(doc, a, COMPLETED, reason)

    def _final(self, doc: dict, a: dict, outcome: str, reason: str) -> dict:
        a.update(outcome=outcome, final=True, missing=[], reason=reason, settled_at=self.clock())
        doc = self._save_attempt(doc, a, (f"attempt_{outcome}", reason))
        return self._record_outcome(doc, a)

    def _record_outcome(self, doc: dict, a: dict) -> dict:
        """Tell NovaMart, the owner of the attempt, so its own record reaches a final state too."""
        evidence = {"order_state": (a["evidence"].get("order") or {}).get("state"),
                    "confirmation": a.get("verified_result") or a["evidence"].get("confirmation")}
        try:
            self.novamart.record_outcome(a["checkout_id"], a["outcome"], doc["_id"], json.loads(json.dumps(evidence, default=str)))
        except LookupFailed as exc:
            return self._save_attempt(doc, {**a, "outcome_recorded": False},
                                      ("outcome_not_recorded", f"NovaMart did not record the outcome: {exc.message}"))
        return self._save_attempt(doc, {**a, "outcome_recorded": True})

    def _review(self, doc: dict, a: dict, missing: list[str], reason: str) -> dict:
        a.update(outcome=NEEDS_REVIEW, final=False, missing=missing, reason=reason)
        return self._save_attempt(doc, a, ("needs_review", reason))

    def _finish(self, doc: dict) -> dict:
        attempts = [doc["attempts"][cid] for cid in doc["affected_checkout_ids"]]
        counts: dict[str, int] = {}
        for a in attempts:
            counts[a["outcome"]] = counts.get(a["outcome"], 0) + 1
        if all(a.get("final") and a.get("outcome_recorded") for a in attempts):
            phase = "complete"
        elif any(a["outcome"] == NEEDS_REVIEW for a in attempts):
            phase = "needs_review"
        else:
            phase = "reconciling"
        fields: dict[str, Any] = {"phase": phase, "counts": counts}
        events = []
        if phase != doc["phase"]:
            events.append(self._event(f"phase_{phase}", _phase_message(phase, counts, len(attempts))))
        if phase == "complete":
            fields["completed_at"] = self.clock()
            fields.update(self._fix_verification(doc))
        doc = self._update(doc["_id"], fields, events)
        return self._release(doc)

    def _fix_verification(self, doc: dict) -> dict:
        """Link the agent's repair: the fresh checkout that passed is the deployed fix's verification."""
        agent = m.incidents(self.db).find_one({"source": m.SOURCE_LIVE, "live.uptime_incident_id": doc["_id"]},
                                              {"_id": 1, "status": 1, "pr.url": 1})
        if agent is None:
            return {}
        verification = {"fresh_checkout_check_id": doc["fresh_checkout"].get("check_id"),
                        "fresh_checkout_at": doc["fresh_checkout"].get("at"),
                        "interrupted_checkouts_settled": len(doc["affected_checkout_ids"])}
        m.incidents(self.db).update_one({"_id": agent["_id"]}, {"$set": {"deployed_fix_verified": verification}})
        m.log_step(agent["_id"], "deployed_fix_verified", "completed", self.worker_id, verification, db=self.db)
        return {"agent_incident_id": agent["_id"], "fix_verification": {**verification,
                                                                        "pr_url": (agent.get("pr") or {}).get("url")}}

    def _release(self, doc: dict) -> dict:
        released = self.coll.find_one_and_update({"_id": doc["_id"], "lease.owner": self.worker_id},
                                                 {"$unset": {"lease": ""}}, return_document=ReturnDocument.AFTER)
        return released or doc

    def run_forever(self, poll_seconds: float = POLL_SECONDS) -> None:
        print(f"OpsRelay recovery worker {self.worker_id} starting", flush=True)
        while True:
            try:
                for doc in self.poll():
                    print(f"[recovery] {doc['_id']}: {doc['phase']} {doc.get('counts', {})}", flush=True)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                print(f"[recovery] loop error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(poll_seconds)


def _describe(confirmation: dict | None, now: datetime) -> dict:
    if confirmation is None:
        return {"state": "absent", "checked_at": now}
    return {"state": "found", "status": confirmation.get("status"), "confirmation_id": confirmation.get("confirmation_id"),
            "order_id": confirmation.get("order_id"), "delivered_at": confirmation.get("delivered_at"), "checked_at": now}


def _phase_message(phase: str, counts: dict[str, int], total: int) -> str:
    parts = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items())) or "none affected"
    if phase == "complete":
        return f"All {total} interrupted checkout(s) have a verified outcome ({parts})."
    if phase == "needs_review":
        return f"Some interrupted checkouts need review ({parts})."
    return f"Reconciling interrupted checkouts ({parts})."


def status(incident_id: str, db: Database | None = None, novaconnect: NovaConnectRecords | None = None) -> dict:
    """What happened to one incident's recovery, with a live confirmation count per order."""
    doc = m.checkout_recoveries(db).find_one({"_id": incident_id})
    if doc is None:
        raise SystemExit(f"no recovery recorded for {incident_id}")
    novaconnect = novaconnect or NovaConnectRecords()
    rows = []
    for cid in doc["affected_checkout_ids"]:
        a = doc["attempts"][cid]
        try:
            count = len(novaconnect.confirmations_for_order(a["order_id"])) if a.get("order_id") else 0
        except LookupFailed as exc:
            count = f"unknown ({exc.kind})"
        rows.append({"checkout_id": cid, "order_id": a.get("order_id"), "outcome": a["outcome"], "reason": a["reason"],
                     "missing": a.get("missing"), "confirmations_for_order": count})
    return {"incident_id": doc["incident_id"], "phase": doc["phase"], "workers": doc.get("workers", []),
            "attempts": rows, "events": [{"at": e["at"], "worker_id": e["worker_id"], "message": e["message"]}
                                          for e in doc.get("events", [])]}


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m agents.recovery")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("worker", help="reconcile recovering incidents forever")
    sub.add_parser("once", help="one pass, then exit")
    s = sub.add_parser("status", help="show one incident's recovery")
    s.add_argument("incident_id")
    sub.add_parser("arm-crash", help=f"exit the next recovery worker once at '{CRASH_POINT}'")
    a = p.parse_args()
    if a.cmd == "worker":
        RecoveryWorker().run_forever()
    elif a.cmd == "once":
        for doc in RecoveryWorker().poll():
            print(f"{doc['_id']}: {doc['phase']} {doc.get('counts', {})}")
    elif a.cmd == "status":
        print(json.dumps(status(a.incident_id), indent=2, default=str))
    elif a.cmd == "arm-crash":
        print(f"armed: {arm_crash()}")


if __name__ == "__main__":
    main()
