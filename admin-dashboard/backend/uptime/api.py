"""Dashboard API (read and mutation endpoints are open; keep it on localhost or behind your own auth).

Every response is ``{"data": ..., "meta": {"generated_at", "storage"}}``; every error is
``{"error": {"code", "message"}}``. No secrets (MongoDB URI) are ever returned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Generic, Literal, TypeVar

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__, assistant, bad_deploys, demo_outages, demo_traffic, walkthrough
from .config import MonitorSettings, load_settings
from .dependencies import DependencyCheck, check_dependencies
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


RecoveryStatus = Literal["not_needed", "waiting", "reconciling", "needs_review", "complete"]


class IncidentOut(Incident):
    duration_seconds: float
    service_name: str
    related_ci: list[dict[str, Any]] = Field(default_factory=list)
    # Two separate statuses: the service's health now, and the recovery of what the outage interrupted.
    service_health: Status = "unknown"
    recovery_status: RecoveryStatus = "not_needed"
    recovery: dict[str, Any] | None = None


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
    # After the agent's PR is merged, whether a person confirmed that it fixed the problem.
    fix_confirmation: Literal["confirmed", "disputed"] | None = None


class LearningStrategy(BaseModel):
    app: str
    action: str
    tries: int
    successes: float
    success_rate: float
    real_tries: int


class LearningCurvePoint(BaseModel):
    epoch: int
    incidents: str
    first_try_rate: float
    avg_attempts: float


class LearningTraining(BaseModel):
    at: datetime
    embedding_model: str | None = None
    accuracy_before: float
    accuracy_after: float
    per_bug: dict[str, float] = {}
    eval_incidents: int | None = None
    curve: list[LearningCurvePoint] = []
    mock_incidents: int | None = None
    epochs: int | None = None
    experiences: int | None = None
    real_experiences: int | None = None


class LearningDecision(BaseModel):
    incident_id: str
    app: str | None
    source: str
    status: str | None
    created_at: datetime | None
    action: str | None
    expected_success: float | None
    hinted: bool
    pr_url: str | None = None


class VectorIndex(BaseModel):
    collection: str
    name: str | None
    status: str | None
    queryable: bool


class Learning(BaseModel):
    available: bool
    totals: dict[str, int] = {}
    strategies: list[LearningStrategy] = []
    training: LearningTraining | None = None
    decisions: list[LearningDecision] = []
    vector_indexes: list[VectorIndex] = []


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
    scenario: Literal["payment", "images", "inventory", "send", "receive", "both", "confirmation_timeout"]


class BadDeployIn(BaseModel):
    scenario: int = Field(ge=1, le=4)


class FixConfirmationIn(BaseModel):
    resolved: bool


class DemoCheckoutsIn(BaseModel):
    count: int = Field(default=4, ge=1, le=10)


# -------------------------------------------------------------------- the app
def create_app(
    storage: Storage | None = None,
    settings: MonitorSettings | None = None,
    *,
    embed_worker: bool = False,
    clock=utcnow,
    monitor_kwargs: dict[str, Any] | None = None,
    assistant_transport: Any = None,
    demo_controls: bool | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    # Demo mutations push bugs to main and rewrite incident state: off unless explicitly a dev or demo deployment.
    if demo_controls is None:
        demo_controls = os.environ.get("OPSRELAY_ENV") in ("development", "demo")
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
    dependency_lock = asyncio.Lock()
    dependency_cache: list[DependencyCheck] | None = None
    dependency_expires_at = 0.0
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

    def require_demo() -> None:
        if not demo_controls:
            raise HTTPException(404, "Demo controls are off. They run only with OPSRELAY_ENV=development or demo.")

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
        recovery = storage.get_recovery(incident.id) if (related or incident.recovery_phase) else None
        state = storage.get_state(incident.service_id)
        return IncidentOut(**incident.model_dump(), duration_seconds=incident.elapsed_seconds(now),
                           service_name=names.get(incident.service_id, incident.service_id), related_ci=related_ci,
                           service_health=state.status if state else "unknown",
                           recovery_status=recovery_status(incident, recovery), recovery=recovery)

    def recovery_status(incident: Incident, recovery: dict[str, Any] | None) -> str:
        if incident.recovery_phase == "recovering":
            phase = (recovery or {}).get("phase")
            if phase is None or phase == "collecting":
                return "waiting"
            return phase if phase in ("needs_review", "complete") else "reconciling"
        return "complete" if recovery and recovery.get("phase") == "complete" else "not_needed"

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
        elif status in ("awaiting_merge", "reported"):
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
                             pr_url=pr_url if isinstance(pr_url, str) and pr_url.startswith("https://") else None,
                             fix_confirmation=fix_confirmation(incident))

    def fix_confirmation(incident: Incident) -> str | None:
        answers = [e.type for e in incident.events if e.type in ("fix_confirmed", "fix_disputed")]
        return None if not answers else "confirmed" if answers[-1] == "fix_confirmed" else "disputed"

    def assistant_context(focus: str | None, now: datetime) -> dict[str, Any]:
        """What the dashboard assistant may know: status, open incidents and the agent's findings."""
        incidents = storage.list_incidents(state="open")
        if focus and not any(i.id == focus for i in incidents):
            focused = storage.get_incident(focus)
            if focused is not None:
                incidents.append(focused)
        names = {s.id: s.name for s in services()}
        return {
            "now": now,
            "services": [{"name": o.name, "status": o.current.status, "summary": o.current.summary}
                         for o in (overview(s, "24h", now, bars=False) for s in services())],
            "viewing_incident": focus,
            "incidents": [{
                "id": i.id, "app": names.get(i.service_id, i.service_id), "state": i.state, "severity": i.severity,
                "title": i.title, "started_at": i.started_at, "resolved_at": i.resolved_at,
                "failed_checks": assistant.plain_failures(i.evidence),
                "fix_confirmation_by_person": fix_confirmation(i),
                "repair_agent": storage.get_agent_findings(i.id),
            } for i in incidents],
        }

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

    @app.get("/api/v1/dependencies", response_model=Envelope[list[DependencyCheck]])
    async def get_dependencies():
        nonlocal dependency_cache, dependency_expires_at
        if dependency_cache is None or time.monotonic() >= dependency_expires_at:
            async with dependency_lock:
                if dependency_cache is None or time.monotonic() >= dependency_expires_at:
                    dependency_cache = await check_dependencies()
                    dependency_expires_at = time.monotonic() + 30
        return ok(dependency_cache)

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

    @app.get("/api/v1/learning", response_model=Envelope[Learning])
    def learning():
        """What the RL fix policy has learned, from rl_experiences / rl_training_runs (read-only)."""
        data = storage.get_learning_summary()
        if not data:
            return ok(Learning(available=False))
        decisions = [{**d, "pr_url": d["pr_url"] if isinstance(d.get("pr_url"), str)
                      and d["pr_url"].startswith("https://") else None} for d in data["decisions"]]
        return ok(Learning(available=True, **{**data, "decisions": decisions}))

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

    @app.get("/api/v1/incidents/{incident_id}/walkthrough", response_model=Envelope[dict[str, Any]])
    def incident_walkthrough(incident_id: str):
        """What the monitor, the repair agent and the recovery worker recorded, as ordered steps."""
        incident = incident_or_404(incident_id)
        return ok(walkthrough.build(incident, storage.get_agent_run(incident_id), storage.get_recovery(incident_id), clock()))

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
    @app.post("/api/v1/demo/outages/{service_id}", response_model=Envelope[dict[str, Any]], dependencies=[Depends(require_demo)])
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

    @app.delete("/api/v1/demo/outages/{service_id}", response_model=Envelope[dict[str, Any]], dependencies=[Depends(require_demo)])
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

    # Bad deploys push a real bug to the base branch on GitHub. Responses never name the
    # bug: the repair agent must find it from the evidence.
    @app.post("/api/v1/demo/bad-deploys/{service_id}", response_model=Envelope[dict[str, Any]], dependencies=[Depends(require_demo)])
    def ship_bad_deploy(service_id: str, body: BadDeployIn):
        if service_id not in bad_deploys.APPS:
            raise HTTPException(404, f"Unknown demo service '{service_id}'.")
        service_or_404(service_id)
        try:
            bad_deploys.ship(service_id, body.scenario)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except bad_deploys.BadDeployError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            logger.error("Cannot ship bad deploy for %s: %s", service_id, exc)
            raise HTTPException(503, "The local demo app is unavailable in this checkout.") from exc
        storage.request_check(clock())
        return ok({"service_id": service_id, "requested": True, "branch": bad_deploys.branch()})

    @app.delete("/api/v1/demo/bad-deploys/{service_id}", response_model=Envelope[dict[str, Any]], dependencies=[Depends(require_demo)])
    def restore_bad_deploy(service_id: str):
        if service_id not in bad_deploys.APPS:
            raise HTTPException(404, f"Unknown demo service '{service_id}'.")
        service_or_404(service_id)
        try:
            bad_deploys.restore(service_id)
        except bad_deploys.BadDeployError as exc:
            raise HTTPException(409, str(exc)) from exc
        except OSError as exc:
            logger.error("Cannot restore bad deploy for %s: %s", service_id, exc)
            raise HTTPException(503, "The bad deploy could not be restored.") from exc
        storage.request_check(clock())
        return ok({"service_id": service_id, "requested": True})

    @app.post("/api/v1/demo/checkouts", response_model=Envelope[list[dict[str, Any]]], dependencies=[Depends(require_demo)])
    async def demo_checkouts(body: DemoCheckoutsIn):
        """Place customer checkouts on NovaMart (each retried once with the same checkout ID on failure)."""
        return ok(await demo_traffic.place_checkouts(service_or_404("novamart"), body.count))

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

    @app.post("/api/v1/incidents/{incident_id}/fix-confirmation", response_model=Envelope[IncidentOut])
    def confirm_fix(incident_id: str, body: FixConfirmationIn):
        """A person says whether the agent's merged PR fixed the problem.

        Yes applies the fix to the running checkout (the bad deploy is restored locally; main
        already has the fix, so nothing is pushed) and asks for a fresh check, which resolves the
        incident once the probe passes. No records it and leaves the incident open.
        """
        incident = incident_or_404(incident_id)
        findings = storage.get_agent_findings(incident_id)
        if not findings or findings.get("status") != "resolved":
            raise HTTPException(409, "The repair PR for this incident has not been merged yet.")
        if body.resolved and incident.service_id in bad_deploys.APPS:
            try:
                bad_deploys.restore(incident.service_id, keep_faults=True)
            except bad_deploys.BadDeployError as exc:
                raise HTTPException(409, str(exc)) from exc
            except OSError as exc:
                logger.error("Cannot apply the merged fix for %s: %s", incident.service_id, exc)
                raise HTTPException(503, "The merged fix could not be applied to the running app.") from exc
        now = clock()
        incident.events.append(IncidentEvent(
            at=now, type="fix_confirmed" if body.resolved else "fix_disputed",
            message="A person confirmed the merged fix resolved the problem; applying it to the running app."
            if body.resolved else "A person reported the problem is still happening after the fix was merged."))
        storage.save_incident(incident)
        storage.request_check(now)
        return ok(incident_out(incident, now))

    @app.post("/api/v1/incidents/{incident_id}/fix-merged", response_model=Envelope[IncidentOut], dependencies=[Depends(require_demo)])
    def mark_fix_merged(incident_id: str):
        """A person says the fix for this incident is merged into main.

        Like a confirmed fix, the running checkout gets the fix (the bad deploy is restored locally,
        and reverted on main only if the bug is somehow still there) and a fresh check resolves the
        incident once the probe passes.
        """
        incident = incident_or_404(incident_id)
        if incident.state != "open":
            raise HTTPException(409, "This incident is already resolved.")
        if incident.service_id in bad_deploys.APPS:
            try:
                bad_deploys.restore(incident.service_id, keep_faults=True)
            except bad_deploys.BadDeployError as exc:
                raise HTTPException(409, str(exc)) from exc
            except OSError as exc:
                logger.error("Cannot apply the merged fix for %s: %s", incident.service_id, exc)
                raise HTTPException(503, "The merged fix could not be applied to the running app.") from exc
        now = clock()
        incident.events.append(IncidentEvent(
            at=now, type="fix_merged", message="The fix has been merged into main and is being applied to the running app."))
        storage.save_incident(incident)
        storage.request_check(now)
        return ok(incident_out(incident, now))

    @app.post("/api/v1/assistant/chat", response_model=Envelope[dict[str, Any]])
    async def assistant_chat(body: assistant.ChatIn):
        try:
            reply = await assistant.answer(assistant_context(body.incident_id, clock()), body.messages,
                                           transport=assistant_transport)
        except assistant.AssistantError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return ok({"reply": reply})

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
