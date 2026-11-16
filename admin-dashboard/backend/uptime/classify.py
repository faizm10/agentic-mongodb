"""Turn three probe results into one service status.

    operational  frontend reachable, availability OK, functional probe healthy
    degraded     frontend and availability OK, but the functional probe fails
                 (HTTP 200 on the homepage does NOT mean the service is healthy)
    down         availability check fails or the frontend is unreachable
    unknown      not enough information (no check yet, stale data, monitor error)
"""

from __future__ import annotations

from .models import FailureType, InvestigationRoute, ProbeResult, Status


def classify(frontend: ProbeResult, availability: ProbeResult | None,
             functional: ProbeResult | None) -> tuple[Status, FailureType | None, str]:
    """availability and functional are None for a site without those URLs."""
    if availability and not availability.ok:
        kind = availability.error_kind
        if kind in ("connection_refused", "connection_error"):
            return "down", "backend_unreachable", f"Backend unreachable at {availability.url} ({availability.error})."
        if kind == "timeout":
            return "down", "backend_timeout", f"Backend availability check timed out ({availability.url})."
        return "down", "availability_http_error", f"Availability check failed: {availability.error}."
    if not frontend.ok:
        kind = frontend.error_kind
        if kind in ("connection_refused", "connection_error"):
            return "down", "frontend_unreachable", f"Website unreachable at {frontend.url} ({frontend.error})."
        if kind == "timeout":
            return "down", "frontend_timeout", f"Website timed out ({frontend.url})."
        return "down", "frontend_http_error", f"Website returned {frontend.error}."
    if functional and not functional.ok:
        if functional.error_kind == "probe_failed":
            failed = ", ".join(functional.details.get("failed_checks") or []) or "unknown checks"
            return "degraded", "functional_probe_failed", f"Website is up but the functional probe failed: {failed}."
        if functional.error_kind == "timeout":
            return "degraded", "functional_probe_timeout", "Website is up but the functional probe timed out."
        return "degraded", "functional_probe_error", f"Website is up but the functional probe errored: {functional.error}."
    if availability is None:
        return "operational", None, "Website is up."
    if functional is None:
        return "operational", None, "Website and health check are up."
    return "operational", None, "Website, availability and functional probe all healthy."


def investigation_route(failure_type: FailureType | None) -> tuple[InvestigationRoute, str]:
    """Different failures need different investigations; an outage is not assumed to be a code bug.

    Route names match agents/live_incident.py so the AI pipeline can consume them directly.
    """
    if failure_type in ("functional_probe_failed",):
        return "investigate", (
            "Service is reachable but a business-rule probe fails. Compare probe evidence with recent changes and CI; "
            "a code regression is a likely suspect but must be reproduced before any repair."
        )
    if failure_type in ("functional_probe_timeout", "functional_probe_error"):
        return "investigate", (
            "Service is reachable but its functional probe did not complete. Check dependencies and logs first; "
            "the cause is unknown."
        )
    return "service_health", (
        "Service is unreachable or its availability check fails. Check the process, deployment, host and network; "
        "do not start with code repair."
    )
