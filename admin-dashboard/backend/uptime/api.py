"""Dashboard API (read and mutation endpoints are open; keep it on localhost or behind your own auth).

Every response is ``{"data": ..., "meta": {"generated_at", "storage"}}``; every error is
``{"error": {"code", "message"}}``. No secrets (MongoDB URI) are ever returned.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Generic, Literal, TypeVar

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__, demo_outages
from .config import MonitorSettings, load_settings
from .handoff import build_handoff
from .models import Incident, IncidentEvent, IncidentLink, ServiceConfig, ServiceState, Status, WorkerState
from .storage import open_storage
from .storage.base import Storage
from .uptime_calc import WINDOWS, compute_uptime, latency_series, needed_since, uptime_bars
from .worker import UptimeMonitor, utcnow

logger = logging.getLogger("opsrelay.uptime.api")
T = TypeVar("T")
Window = Literal["24h", "7d", "30d"]


# ------------------------------------------------------------------ envelopes
class Meta(BaseModel):
    generated_at: datetime
    storage: dict[str, Any]
    version: str = __version__


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta


class CurrentStatus(BaseModel):
    status: Status
    summary: str
    since: datetime | None
    last_checked_at: datetime | None
    last_success_at: datetime | None
    stale: bool
    failure_type: str | None
    open_incident_id: str | None


class PublicServiceConfig(BaseModel):
    id: str
    name: str
    app: str
    description: str
    frontend_url: str
    backend_url: str
    availability_path: str
    functional_path: str
    functional_method: str
    interval_seconds: int
    timeout_seconds: float
    functional_timeout_seconds: float
    failure_threshold: int
    recovery_threshold: int
    enabled: bool


class ServiceOverview(BaseModel):
    id: str
    name: str
    app: str
    description: str
    current: CurrentStatus
    uptime: dict[str, Any]
    bars: list[dict[str, Any]] | None = None


class IncidentOut(Incident):
    duration_seconds: float
    service_name: str
    related_ci: list[dict[str, Any]] = Field(default_factory=list)


class MonitoringHealth(BaseModel):
    status: Literal["running", "stale", "not_started"]
    message: str
    interval_seconds: int
    worker: WorkerState | None
    lease_held: bool
    next_cycle_expected_at: datetime | None
    total_checks: int
    storage: dict[str, Any]


class AgentAction(BaseModel):
    stage: str
    outcome: str
    at: datetime


class AgentActivity(BaseModel):
    incident_id: str
    service_name: str
    state: Literal["unavailable", "waiting", "working", "awaiting_review", "needs_attention", "finished"]
    stage: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    latest_action: AgentAction | None = None
    pr_url: str | None = None


class Summary(BaseModel):
    overall: dict[str, Any]
    window: Window
    services: list[ServiceOverview]
    active_incidents: list[IncidentOut]
    recent_incidents: list[IncidentOut]
    monitoring: MonitoringHealth
    agent_activity: list[AgentActivity]


class AckIn(BaseModel):
    by: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class LinkIn(BaseModel):
    kind: Literal["github_actions_run", "repair_pr", "other"]
    url: str = Field(pattern=r"^https://", max_length=500)
    label: str = Field(default="", max_length=120)
    source: str = Field(default="manual", max_length=60)


class ServicePatch(BaseModel):
    enabled: bool


class DemoOutageIn(BaseModel):
    scenario: Literal["payment", "images", "inventory", "send", "receive", "both"]


# -------------------------------------------------------------------- the app
def create_app(
    storage: Storage | None = None,
    settings: MonitorSettings | None = None,
    *,
    embed_worker: bool = False,
    clock=utcnow,
    monitor_kwargs: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    storage = storage or open_storage(retention_days=settings.retention_days,
                                      incident_retention_days=settings.incident_retention_days)
    for service in settings.services:  # make configuration visible even before the worker runs
        storage.upsert_service(service)
    stop = asyncio.Event()
    monitor = UptimeMonitor(storage, settings.services, interval_seconds=settings.interval_seconds,
                            lease_ttl_seconds=settings.lease_ttl, retention_days=settings.retention_days,
                            incident_retention_days=settings.incident_retention_days, clock=clock,
                            **(monitor_kwargs or {})) if embed_worker else None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(monitor.run_forever(stop)) if monitor else None
        yield
        stop.set()
        if task:
            await task

    app = FastAPI(title="OpsRelay Uptime API", version=__version__, lifespan=lifespan)
    app.state.storage = storage
    app.state.monitor = monitor
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in os.environ.get("OPSRELAY_DASHBOARD_ORIGINS", "http://localhost:3300,http://127.0.0.1:3300").split(",") if o],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------- helpers
    def meta() -> dict[str, Any]:
        return {"generated_at": clock(), "storage": storage.describe(), "version": __version__}

    def ok(data: Any) -> dict[str, Any]:
        return {"data": data, "meta": meta()}

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT"}.get(exc.status_code, "ERROR")
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": exc.detail}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        message = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_FAILED", "message": message}})

    def services() -> list[ServiceConfig]:
        stored = {s.id: s for s in storage.list_services()}
        return [stored.get(s.id, s) for s in settings.services]

    def service_or_404(service_id: str) -> ServiceConfig:
        for s in services():
            if s.id == service_id:
                return s
        raise HTTPException(404, f"Unknown service '{service_id}'.")

    def current(service: ServiceConfig, now: datetime) -> CurrentStatus:
        state = storage.get_state(service.id) or ServiceState(service_id=service.id)
        stale_after = timedelta(seconds=settings.stale_after_intervals * service.interval_seconds)
        if not service.enabled:
            status, summary, stale = "unknown", "Monitoring is paused for this service.", False
        elif state.last_checked_at is None:
            status, summary, stale = "unknown", "No checks recorded yet.", False
        elif now - state.last_checked_at > stale_after:
            age = int((now - state.last_checked_at).total_seconds())
            status, summary, stale = "unknown", f"Last check was {age}s ago; the monitoring worker may be stopped.", True
        else:
            status, summary, stale = state.status, state.summary, False
        return CurrentStatus(status=status, summary=summary, since=state.status_since, last_checked_at=state.last_checked_at,
                             last_success_at=state.last_success_at, stale=stale, failure_type=state.failure_type,
                             open_incident_id=state.open_incident_id)

    def uptime(service: ServiceConfig, window: str, now: datetime, *, bars: bool) -> tuple[dict, list | None]:
        samples = storage.list_samples(service.id, needed_since(window, now, service.interval_seconds), now)
        stats = compute_uptime(samples, window, now, storage.first_check_at(service.id))
        return stats, (uptime_bars(samples, window, now) if bars else None)

    def overview(service: ServiceConfig, window: str, now: datetime, *, bars: bool) -> ServiceOverview:
        stats, b = uptime(service, window, now, bars=bars)
        return ServiceOverview(id=service.id, name=service.name, app=service.app, description=service.description,
                               current=current(service, now), uptime=stats, bars=b)

    def incident_out(incident: Incident, now: datetime, *, related: bool = False) -> IncidentOut:
        names = {s.id: s.name for s in services()}
        related_ci = []
        if related:
            for doc in storage.find_agent_incidents(incident.app, incident.started_at, incident.resolved_at or now):
                related_ci.append({
                    "id": str(doc.get("_id")), "status": doc.get("status"), "stage": doc.get("stage"),
                    "run_url": doc.get("html_url"), "pr_url": (doc.get("pr") or {}).get("url"),
                    "commit_sha": doc.get("commit_sha"), "created_at": doc.get("created_at"),
                })
        return IncidentOut(**incident.model_dump(), duration_seconds=incident.elapsed_seconds(now),
                           service_name=names.get(incident.service_id, incident.service_id), related_ci=related_ci)

    def agent_activity(incident: Incident, now: datetime) -> AgentActivity:
        name = next((s.name for s in services() if s.id == incident.service_id), incident.service_id)
        run = storage.get_agent_activity(incident.id)
        if run is None:
            state = "unavailable" if storage.describe()["backend"] != "mongodb" else "waiting"
            return AgentActivity(incident_id=incident.id, service_name=name, state=state)
        status = run.get("status")
        lease = run.get("lease_expires_at")
        if status == "open":
            state = "working" if lease and lease > now else "waiting"
        elif status == "awaiting_merge":
            state = "awaiting_review"
        elif status in ("needs_review", "failed", "rejected"):
            state = "needs_attention"
        elif status == "resolved":
            state = "finished"
        else:
            state = "waiting"
        pr_url = run.get("pr_url")
        return AgentActivity(incident_id=incident.id, service_name=name, state=state,
                             stage=run.get("stage"), started_at=run.get("created_at"),
                             updated_at=run.get("updated_at"), latest_action=run.get("latest_action"),
                             pr_url=pr_url if isinstance(pr_url, str) and pr_url.startswith("https://") else None)

    def monitoring(now: datetime) -> MonitoringHealth:
        worker = storage.get_worker()
        interval = settings.interval_seconds
        lease_held = bool(worker and worker.owner and worker.lease_expires_at and worker.lease_expires_at > now)
        last = worker.last_cycle_started_at if worker else None
        if last is None:
            status, message = "not_started", "The monitoring worker has not completed a cycle yet."
        elif now - last > timedelta(seconds=settings.stale_after_intervals * interval):
            status, message = "stale", f"No cycle for {int((now - last).total_seconds())}s; start the worker (python -m uptime worker)."
        else:
            status, message = "running", f"Checking {len(services())} services every {interval}s."
        return MonitoringHealth(status=status, message=message, interval_seconds=interval, worker=worker,
                                lease_held=lease_held, next_cycle_expected_at=(last + timedelta(seconds=interval)) if last else None,
                                total_checks=storage.count_checks(), storage=storage.describe())

    # ------------------------------------------------------------- routes
    @app.get("/api/v1/health", response_model=Envelope[dict[str, Any]])
    def api_health():
        return ok({"status": "ok", "service": "opsrelay-uptime-api"})

    @app.get("/api/v1/monitoring", response_model=Envelope[MonitoringHealth])
    def get_monitoring():
        return ok(monitoring(clock()))

    @app.get("/api/v1/summary", response_model=Envelope[Summary])
    def summary(window: Window = "24h"):
        now = clock()
        overviews = [overview(s, window, now, bars=True) for s in services()]
        statuses = [o.current.status for o in overviews]
        if "down" in statuses:
            overall = {"status": "down", "label": "Major outage"}
        elif "degraded" in statuses:
            overall = {"status": "degraded", "label": "Partial degradation"}
        elif statuses and all(s == "operational" for s in statuses):
            overall = {"status": "operational", "label": "All systems operational"}
        else:
            overall = {"status": "unknown", "label": "Monitoring data incomplete"}
        affected = [o.name for o in overviews if o.current.status in ("down", "degraded")]
        overall["affected_services"] = affected
        open_incidents = storage.list_incidents(state="open")
        active = [incident_out(i, now) for i in open_incidents]
        recent = [incident_out(i, now) for i in storage.list_incidents(state="resolved", limit=10)]
        return ok(Summary(overall=overall, window=window, services=overviews, active_incidents=active,
                          recent_incidents=recent, monitoring=monitoring(now),
                          agent_activity=[agent_activity(i, now) for i in open_incidents]))

    @app.get("/api/v1/services", response_model=Envelope[list[ServiceOverview]])
    def list_services(window: Window = "24h"):
        now = clock()
        return ok([overview(s, window, now, bars=False) for s in services()])

    @app.get("/api/v1/services/{service_id}", response_model=Envelope[dict[str, Any]])
    def service_detail(service_id: str):
        now = clock()
        service = service_or_404(service_id)
        windows = {w: uptime(service, w, now, bars=False)[0] for w in WINDOWS}
        incidents = storage.list_incidents(service_id=service_id, limit=20)
        return ok({
            "service": PublicServiceConfig(**service.model_dump()).model_dump(),
            "current": current(service, now).model_dump(),
            "uptime": windows,
            "incidents": [incident_out(i, now).model_dump() for i in incidents],
            "incident_ids": [i.id for i in incidents],
        })

    @app.get("/api/v1/services/{service_id}/uptime", response_model=Envelope[dict[str, Any]])
    def service_uptime(service_id: str, window: Window = "24h"):
        now = clock()
        stats, bars = uptime(service_or_404(service_id), window, now, bars=True)
        return ok({"stats": stats, "bars": bars})

    @app.get("/api/v1/services/{service_id}/checks", response_model=Envelope[list[dict[str, Any]]])
    def service_checks(service_id: str, limit: int = Query(default=30, ge=1, le=500), before: datetime | None = None):
        service_or_404(service_id)
        return ok([c.model_dump() for c in storage.list_checks(service_id, limit=limit, before=before)])

    @app.get("/api/v1/services/{service_id}/latency", response_model=Envelope[list[dict[str, Any]]])
    def service_latency(service_id: str, window: Window = "24h", points: int = Query(default=120, ge=10, le=500)):
        now = clock()
        service = service_or_404(service_id)
        samples = storage.list_samples(service_id, needed_since(window, now, service.interval_seconds), now)
        return ok(latency_series(samples, window, now, points))

    @app.get("/api/v1/incidents", response_model=Envelope[list[IncidentOut]])
    def list_incidents(state: Literal["open", "resolved", "all"] = "all", service_id: str | None = None,
                       limit: int = Query(default=50, ge=1, le=200)):
        now = clock()
        items = storage.list_incidents(service_id=service_id, state=None if state == "all" else state, limit=limit)
        return ok([incident_out(i, now) for i in items])

    def incident_or_404(incident_id: str) -> Incident:
        incident = storage.get_incident(incident_id)
        if incident is None:
            raise HTTPException(404, f"Unknown incident '{incident_id}'.")
        return incident

    @app.get("/api/v1/incidents/{incident_id}", response_model=Envelope[IncidentOut])
    def get_incident(incident_id: str):
        return ok(incident_out(incident_or_404(incident_id), clock(), related=True))

    @app.get("/api/v1/incidents/{incident_id}/evidence", response_model=Envelope[dict[str, Any]])
    def incident_evidence(incident_id: str):
        incident = incident_or_404(incident_id)
        service = next((s for s in services() if s.id == incident.service_id), None)
        return ok(build_handoff(incident, service, clock()))

    # ---------------------------------------------------------- mutations
    @app.post("/api/v1/demo/outages/{service_id}", response_model=Envelope[dict[str, Any]])
    def start_demo_outage(service_id: str, body: DemoOutageIn):
        if service_id not in demo_outages.SCENARIOS:
            raise HTTPException(404, f"Unknown demo service '{service_id}'.")
        service_or_404(service_id)
        try:
            demo_outages.activate(service_id, body.scenario)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            logger.error("Cannot start demo outage for %s: %s", service_id, exc)
            raise HTTPException(503, "The local demo app is unavailable in this checkout.") from exc
        storage.request_check(clock())
        return ok({"service_id": service_id, "requested": True})

    @app.delete("/api/v1/demo/outages/{service_id}", response_model=Envelope[dict[str, Any]])
    def stop_demo_outage(service_id: str):
        if service_id not in demo_outages.SCENARIOS:
            raise HTTPException(404, f"Unknown demo service '{service_id}'.")
        service_or_404(service_id)
        try:
            demo_outages.clear(service_id)
        except OSError as exc:
            logger.error("Cannot clear demo outage for %s: %s", service_id, exc)
            raise HTTPException(503, "The local demo outage could not be cleared.") from exc
        storage.request_check(clock())
        return ok({"service_id": service_id, "requested": True})

    @app.patch("/api/v1/services/{service_id}", response_model=Envelope[PublicServiceConfig])
    def patch_service(service_id: str, body: ServicePatch):
        service_or_404(service_id)
        updated = storage.set_service_enabled(service_id, body.enabled)
        return ok(PublicServiceConfig(**updated.model_dump()))

    @app.post("/api/v1/monitor/check-now", response_model=Envelope[dict[str, Any]])
    async def check_now():
        if monitor is not None:
            result = await monitor.run_cycle(scheduled=False)
            if result is None:
                raise HTTPException(409, "A monitoring cycle is already running.")
            return ok({"ran": True, "statuses": result["statuses"], "recorded": result["recorded"]})
        storage.request_check(clock())
        return ok({"ran": False, "requested": True, "message": "The worker will run a check within ~1s."})

    @app.post("/api/v1/incidents/{incident_id}/acknowledge", response_model=Envelope[IncidentOut])
    def acknowledge(incident_id: str, body: AckIn):
        incident = incident_or_404(incident_id)
        now = clock()
        incident.acknowledged_at, incident.acknowledged_by = now, body.by
        incident.events.append(IncidentEvent(at=now, type="acknowledged", message=f"Acknowledged by {body.by}. {body.note}".strip()))
        storage.save_incident(incident)
        return ok(incident_out(incident, now))

    @app.post("/api/v1/incidents/{incident_id}/links", response_model=Envelope[IncidentOut])
    def add_link(incident_id: str, body: LinkIn):
        incident = incident_or_404(incident_id)
        now = clock()
        if not any(link.url == body.url for link in incident.links):
            incident.links.append(IncidentLink(**body.model_dump()))
            incident.events.append(IncidentEvent(at=now, type="link_added", message=f"{body.kind}: {body.url}"))
            storage.save_incident(incident)
        return ok(incident_out(incident, now))

    return app
