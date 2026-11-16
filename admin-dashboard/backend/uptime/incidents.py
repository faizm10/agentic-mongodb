"""Incident lifecycle driven by check results.

* A service has at most one open incident (enforced in storage, not just here).
* Consecutive failing checks during one outage update that incident; they never open new ones.
* degraded -> down escalates the same incident (and down -> degraded de-escalates it).
* The first operational check (``recovery_threshold``) resolves it and records the duration,
  unless the service declares records an outage can interrupt (``recovery``). Then the
  service is operational again but the incident stays open in its ``recovering`` phase
  until the OpsRelay recovery worker reports every interrupted record settled
  (agents/recovery.py). A failing check during recovery puts the incident back in outage.
* ``unknown`` results (monitor errors) neither open nor resolve incidents.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .classify import investigation_route
from .models import CheckRecord, Incident, IncidentEvent, ServiceConfig, ServiceState
from .storage.base import Storage

SEVERITY_ORDER = {"degraded": 1, "down": 2}


def incident_id(service: ServiceConfig, started_at: datetime) -> str:
    return f"UPT-{service.id}-{started_at:%Y%m%d-%H%M%S}"


def _evidence(check: CheckRecord) -> dict[str, Any]:
    probes = {p.kind: p.model_dump(mode="json") for p in (check.frontend, check.availability, check.functional) if p}
    return {"check_id": check.id, "checked_at": check.checked_at.isoformat(), "status": check.status,
            "failure_type": check.failure_type, "summary": check.summary, "probes": probes}


def _title(service: ServiceConfig, severity: str) -> str:
    return f"{service.name} {'is down' if severity == 'down' else 'is degraded'}"


def apply_check(storage: Storage, service: ServiceConfig, check: CheckRecord) -> list[IncidentEvent]:
    """Update current status and incidents for one *newly recorded* check. Returns incident events."""
    state = storage.get_state(service.id) or ServiceState(service_id=service.id)
    events: list[IncidentEvent] = []
    at = check.checked_at

    if check.status != state.status:
        state.status_since = at
    state.status = check.status
    state.summary = check.summary
    state.failure_type = check.failure_type
    state.last_checked_at = at
    state.last_check_id = check.id

    if check.status == "unknown":
        storage.put_state(state)
        return events

    failing = check.status in ("degraded", "down")
    if failing:
        state.consecutive_failures += 1
        state.consecutive_successes = 0
    else:
        state.consecutive_successes += 1
        state.consecutive_failures = 0
        state.last_success_at = at

    incident = storage.get_open_incident(service.id)
    if failing:
        severity = check.status
        if incident is None and state.consecutive_failures >= service.failure_threshold:
            route, reason = investigation_route(check.failure_type)
            incident = Incident(
                id=incident_id(service, at), service_id=service.id, app=service.app, state="open",
                severity=severity, peak_severity=severity, failure_type=check.failure_type,
                investigation_route=route, title=_title(service, severity), summary=check.summary,
                started_at=at, last_failure_at=at, failed_check_count=1,
                first_check_id=check.id, last_check_id=check.id,
                evidence={"route_reason": reason, "first_failure": _evidence(check), "latest_failure": _evidence(check)},
                events=[IncidentEvent(at=at, type="opened", status=check.status, message=check.summary, check_id=check.id)],
            )
            if storage.create_incident(incident):
                events.append(incident.events[0])
            else:  # lost a race with another writer: update the incident that already exists
                incident = storage.get_open_incident(service.id)
        elif incident is not None:
            if incident.recovery_phase is not None:
                incident.recovery_phase = None
                incident.service_recovered_at = None
                ev = IncidentEvent(at=at, type="relapsed", status=check.status, check_id=check.id,
                                   message=f"Failing again while recovering interrupted records: {check.summary}")
                incident.events.append(ev)
                events.append(ev)
            incident.failed_check_count += 1
            incident.last_failure_at = at
            incident.last_check_id = check.id
            incident.evidence["latest_failure"] = _evidence(check)
            if severity != incident.severity:
                kind = "escalated" if SEVERITY_ORDER[severity] > SEVERITY_ORDER[incident.severity] else "deescalated"
                incident.severity = severity
                incident.title = _title(service, severity)
                if SEVERITY_ORDER[severity] > SEVERITY_ORDER[incident.peak_severity]:
                    incident.peak_severity = severity
                ev = IncidentEvent(at=at, type=kind, status=check.status, message=check.summary, check_id=check.id)
                incident.events.append(ev)
                events.append(ev)
            if check.failure_type != incident.failure_type:
                route, reason = investigation_route(check.failure_type)
                incident.failure_type = check.failure_type
                incident.investigation_route = route
                incident.evidence["route_reason"] = reason
                ev = IncidentEvent(at=at, type="failure_changed", status=check.status,
                                   message=f"Failure changed to {check.failure_type}: {check.summary}", check_id=check.id)
                incident.events.append(ev)
                events.append(ev)
            incident.summary = check.summary
            storage.save_incident(incident)
    elif incident is not None and state.consecutive_successes >= service.recovery_threshold:
        if incident.recovery_phase == "recovering":
            recovery = storage.get_recovery(incident.id) or {}
            if recovery.get("phase") == "complete":
                settled = len(recovery.get("attempts") or [])
                events.append(_resolve(storage, service, incident, check, at,
                                       f"all {settled} interrupted checkout(s) have a verified outcome"))
                incident = None
        elif _needs_recovery(storage, service, incident, check):
            incident.recovery_phase = "recovering"
            incident.service_recovered_at = at
            incident.recovery_check_id = check.id
            pending = (check.functional.details.get("interrupted_checkouts") if check.functional else None) or 0
            ev = IncidentEvent(at=at, type="service_recovered", status="operational", check_id=check.id,
                               message=f"A fresh checkout passed and the service is operational. Recovering "
                                       f"interrupted checkouts ({pending} without a final outcome).")
            incident.events.append(ev)
            events.append(ev)
            storage.save_incident(incident)
        else:
            events.append(_resolve(storage, service, incident, check, at, "all checks passing again"))
            incident = None

    state.open_incident_id = incident.id if incident is not None and incident.state == "open" else None
    storage.put_state(state)
    return events


def _needs_recovery(storage: Storage, service: ServiceConfig, incident: Incident, check: CheckRecord) -> bool:
    if service.recovery != "checkout_attempts" or not storage.supports_recovery():
        return False
    interrupted = (check.functional.details.get("interrupted_checkouts") if check.functional else None) or 0
    return interrupted > 0 or storage.get_recovery(incident.id) is not None


def _resolve(storage: Storage, service: ServiceConfig, incident: Incident, check: CheckRecord, at: datetime,
             why: str) -> IncidentEvent:
    incident.state = "resolved"
    incident.resolved_at = at
    incident.recovery_phase = None
    incident.title = f"{service.name} was {'down' if incident.peak_severity == 'down' else 'degraded'}"
    if incident.service_recovered_at is None:
        incident.recovery_check_id = check.id
    minutes = incident.elapsed_seconds(at) / 60
    ev = IncidentEvent(at=at, type="resolved", status="operational", check_id=check.id,
                       message=f"Recovered: {why} after {minutes:.1f} min.")
    incident.events.append(ev)
    storage.save_incident(incident)
    return ev
