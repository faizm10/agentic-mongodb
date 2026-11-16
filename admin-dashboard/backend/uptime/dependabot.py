"""Open Dependabot alerts on a mapped repository -> one OpsRelay incident per repository.

The scheduled check syncs every mapped repository with its workspace's GitHub token (a fine-grained
token needs "Dependabot alerts: read"). While any alert is open the repository has one open incident
listing them: "down" if any is critical or high, "degraded" otherwise. It resolves once every alert
is fixed or dismissed. Each mapping keeps the last sync result, so a token without access shows up
on the Repositories page instead of failing silently.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from . import repositories
from .models import Incident, IncidentEvent
from .storage.base import Storage, iso

SEVERE = {"critical", "high"}


def service_key(mapping_id: str) -> str:
    return f"{mapping_id}#dependabot"


def _alert(a: dict) -> dict:
    advisory = a.get("security_advisory") or {}
    package = (a.get("dependency") or {}).get("package") or {}
    patched = ((a.get("security_vulnerability") or {}).get("first_patched_version") or {}).get("identifier")
    return {"number": a.get("number"), "package": package.get("name"), "ecosystem": package.get("ecosystem"),
            "manifest": (a.get("dependency") or {}).get("manifest_path"), "severity": advisory.get("severity") or "unknown",
            "summary": advisory.get("summary") or "", "patched_version": patched, "url": a.get("html_url")}


async def sync(storage: Storage, mapping: dict, now: datetime, transport=None) -> dict:
    try:
        gh = repositories.client(storage, mapping["workspace_id"], transport)
        raw = await gh.get(f"{repositories._repo_path(mapping['repo'])}/dependabot/alerts", state="open", per_page=100)
        result = _apply(storage, mapping, [_alert(a) for a in raw], now)
    except repositories.RepoError as exc:
        result = {"result": "skipped", "reason": str(exc)}
    storage.put_doc("repos", mapping["id"], {**mapping, "dependabot": {"checked_at": iso(now), **result}})
    return result


def _apply(storage: Storage, mapping: dict, alerts: list[dict], now: datetime) -> dict:
    key = service_key(mapping["id"])
    incident = storage.get_open_incident(key)
    if not alerts:
        if incident is None:
            return {"result": "clear", "open_alerts": 0}
        incident.state, incident.resolved_at = "resolved", now
        incident.events.append(IncidentEvent(at=now, type="resolved", status="operational",
                                             message="Every Dependabot alert is fixed or dismissed."))
        storage.save_incident(incident)
        return {"result": "resolved", "open_alerts": 0, "incident_id": incident.id}

    severity = "down" if any(a["severity"] in SEVERE for a in alerts) else "degraded"
    n = len(alerts)
    title = f"{mapping['service_name']}: {n} open Dependabot alert{'' if n == 1 else 's'}"
    summary = "; ".join(f"{a['package']} ({a['severity']})" for a in alerts[:5]) + (f"; {n - 5} more" if n > 5 else "")
    evidence = {"source": "dependabot", "repo": mapping["repo"], "alerts": alerts}
    if incident is None:
        incident = Incident(
            id=f"DEP-{hashlib.sha256(key.encode()).hexdigest()[:8]}-{int(now.timestamp())}", service_id=key,
            app=mapping["repo"], state="open", severity=severity, peak_severity=severity, failure_type=None,
            investigation_route="investigate", title=title, summary=summary, started_at=now, last_failure_at=now,
            first_check_id=f"dependabot:{key}", last_check_id=f"dependabot:{key}", evidence=evidence,
            events=[IncidentEvent(at=now, type="opened", status=severity, message=f"Dependabot: {summary}")])
        storage.create_incident(incident)
        return {"result": "created", "open_alerts": n, "incident_id": incident.id}

    if [a["number"] for a in incident.evidence.get("alerts", [])] != [a["number"] for a in alerts]:
        incident.events.append(IncidentEvent(at=now, type="failure_changed", status=severity, message=f"Dependabot: {summary}"))
    incident.title, incident.summary, incident.severity, incident.evidence = title, summary, severity, evidence
    incident.peak_severity = "down" if "down" in (incident.peak_severity, severity) else severity
    incident.last_failure_at = now
    storage.save_incident(incident)
    return {"result": "updated", "open_alerts": n, "incident_id": incident.id}
