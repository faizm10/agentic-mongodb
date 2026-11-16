"""Better Stack incident webhooks -> OpsRelay incidents.

Better Stack doesn't sign webhooks, so each workspace gets its own webhook URL plus a secret that
Better Stack sends as a custom header. The monitor id in the payload (data.relationships.monitor)
picks the mapped service. Every Better Stack incident becomes one OpsRelay incident: a redelivery
of the same event changes nothing, and later events (acknowledged, resolved, reopened) update it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

from .models import Incident, IncidentEvent
from .storage.base import Storage, iso

SECRET_HEADER = "x-opsrelay-secret"


class AlertError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def settings(storage: Storage, ws: str) -> dict:
    conf = storage.get_doc("betterstack", ws)
    if conf is None:
        return {"configured": False}
    return {"configured": True, "webhook_path": f"/api/v1/webhooks/betterstack/{conf['hook_id']}",
            "secret_header": "X-OpsRelay-Secret", "last_delivery": conf.get("last_delivery")}


def rotate(storage: Storage, ws: str, now: datetime) -> dict:
    """Create (or replace) the workspace's webhook URL and secret. The secret is only shown now."""
    old = storage.get_doc("betterstack", ws)
    if old:
        storage.delete_doc("bs_hooks", old["hook_id"])
    hook_id, secret = secrets.token_hex(12), secrets.token_urlsafe(32)
    storage.put_doc("bs_hooks", hook_id, {"workspace_id": ws, "secret": _hash(secret)})
    storage.put_doc("betterstack", ws, {"hook_id": hook_id, "created_at": iso(now)})
    return {**settings(storage, ws), "secret": secret}


def map_monitor(storage: Storage, ws: str, service_id: str, monitor_id: str | None) -> dict:
    mapping = storage.get_doc("repos", f"{ws}:{service_id}")
    if mapping is None:
        raise AlertError(404, f"Unknown service '{service_id}'.")
    if old := mapping.get("betterstack_monitor_id"):
        storage.delete_doc("bs_monitors", f"{ws}:{old}")
    if monitor_id:
        if not storage.insert_doc("bs_monitors", f"{ws}:{monitor_id}", {"service_key": mapping["id"]}):
            raise AlertError(409, f"Better Stack monitor {monitor_id} is already mapped to another service.")
    storage.put_doc("repos", mapping["id"], {**mapping, "betterstack_monitor_id": monitor_id or None})
    return {"service_id": service_id, "betterstack_monitor_id": monitor_id or None}


def _event(attrs: dict) -> tuple[str, str]:
    """Which lifecycle event a delivery describes, and when it happened (the payload has no status field)."""
    if attrs.get("resolved_at"):
        return "resolved", attrs["resolved_at"]
    if attrs.get("acknowledged_at"):
        return "acknowledged", attrs["acknowledged_at"]
    return "started", attrs.get("started_at") or ""


def _when(value: str, now: datetime) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return now


def receive(storage: Storage, hook_id: str, secret: str | None, payload: dict[str, Any], now: datetime) -> dict:
    hook = storage.get_doc("bs_hooks", hook_id)
    if hook is None or not secret or not hmac.compare_digest(_hash(secret), hook["secret"]):
        raise AlertError(401, "Unknown webhook URL or wrong X-OpsRelay-Secret header.")
    ws = hook["workspace_id"]
    result = _apply(storage, ws, payload, now)
    conf = storage.get_doc("betterstack", ws)
    storage.put_doc("betterstack", ws, {**conf, "last_delivery": {"at": iso(now), **result}})
    return result


def _apply(storage: Storage, ws: str, payload: dict[str, Any], now: datetime) -> dict:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    attrs = data.get("attributes") or {}
    if payload.get("event") == "comment":
        return {"result": "ignored", "reason": "Comments don't change incidents."}
    monitor_id = str((((data.get("relationships") or {}).get("monitor") or {}).get("data") or {}).get("id") or "")
    if data.get("type") != "incident" or not data.get("id") or not monitor_id:
        raise AlertError(422, "Expected a Better Stack incident with data.relationships.monitor; "
                              "keep relationships in the webhook template.")
    ref = storage.get_doc("bs_monitors", f"{ws}:{monitor_id}")
    if ref is None:
        return {"result": "ignored", "reason": f"Better Stack monitor {monitor_id} isn't mapped to a service."}
    mapping = storage.get_doc("repos", ref["service_key"])

    event, happened = _event(attrs)
    link = storage.get_doc("alert_incidents", f"{ws}:{data['id']}")
    incident = storage.get_incident(link["incident_id"]) if link else None
    # A reopened incident repeats the original start time, so starts are counted per resolution.
    generation = sum(e.type == "resolved" for e in incident.events) if incident and event == "started" else 0
    if not storage.insert_doc("alert_deliveries", _hash(f"{ws}|{data['id']}|{event}|{happened}|{generation}"), {"at": iso(now)}):
        return {"result": "duplicate", "incident_id": incident.id if incident else None}

    cause = attrs.get("cause") or "Better Stack reported an incident."
    evidence = {"source": "betterstack", "betterstack_incident_id": data["id"], "monitor_id": monitor_id,
                "cause": cause, "url": attrs.get("url"), "response_url": attrs.get("response_url"),
                "screenshot_url": attrs.get("screenshot_url")}
    if incident is None:
        if event == "resolved":
            return {"result": "ignored", "reason": "Resolved before OpsRelay saw it start."}
        incident = Incident(
            id=f"BS-{data['id']}-{_hash(ws)[:6]}", service_id=mapping["id"], app=mapping["repo"], state="open",
            severity="down", peak_severity="down", failure_type=None, investigation_route="investigate",
            title=f"{mapping['service_name']}: {cause}"[:200], summary=cause, started_at=_when(happened, now),
            last_failure_at=now, first_check_id=f"betterstack:{data['id']}", last_check_id=f"betterstack:{data['id']}",
            evidence=evidence, events=[IncidentEvent(at=now, type="opened", status="down", message=f"Better Stack: {cause}")])
        if not storage.create_incident(incident):
            incident = storage.get_open_incident(mapping["id"])
            incident.events.append(IncidentEvent(at=now, type="failure_changed", message=f"Better Stack: {cause}"))
            storage.save_incident(incident)
        storage.put_doc("alert_incidents", f"{ws}:{data['id']}", {"incident_id": incident.id})
        return {"result": "created", "incident_id": incident.id}

    incident.evidence.update(evidence)
    incident.last_failure_at = now
    if event == "resolved":
        incident.state, incident.resolved_at = "resolved", _when(happened, now)
        incident.events.append(IncidentEvent(at=now, type="resolved", status="operational", message="Better Stack resolved the incident."))
    elif event == "acknowledged":
        incident.acknowledged_at = incident.acknowledged_at or _when(happened, now)
        incident.events.append(IncidentEvent(at=now, type="acknowledged", message="Acknowledged in Better Stack."))
    elif incident.state == "resolved":
        incident.state, incident.resolved_at = "open", None
        incident.events.append(IncidentEvent(at=now, type="relapsed", status="down", message=f"Better Stack reopened it: {cause}"))
    else:
        incident.events.append(IncidentEvent(at=now, type="failure_changed", status="down", message=f"Better Stack: {cause}"))
    storage.save_incident(incident)
    return {"result": "updated", "incident_id": incident.id}
