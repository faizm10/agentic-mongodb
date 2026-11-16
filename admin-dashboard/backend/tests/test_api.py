"""Dashboard API: envelope, independent statuses, uptime from stored checks, incidents, demo scenario."""

import asyncio
from datetime import timedelta

import pytest

from fastapi.testclient import TestClient

from uptime.api import create_app
from uptime.worker import UptimeMonitor

from .conftest import SERVICES, make_mongo, make_sqlite, signed_in


@pytest.fixture(params=["sqlite", "mongodb"])
def env(request, tmp_path, settings, clock, apps):
    storage = make_sqlite(tmp_path) if request.param == "sqlite" else make_mongo()
    monitor = UptimeMonitor(storage, SERVICES, interval_seconds=30, clock=clock, sleep=clock.sleep,
                            transport=apps.transport(), worker_id="test-worker")
    storage.acquire_lease("test-worker", clock(), 70)

    def tick(n=1):
        for _ in range(n):
            asyncio.run(monitor.run_cycle())
            clock.advance(30)
            storage.acquire_lease("test-worker", clock(), 70)

    def client(email="owner@example.com"):
        return signed_in(create_app(storage, settings, clock=clock), email)

    return {"storage": storage, "tick": tick, "client": client, "apps": apps, "clock": clock}


def test_envelope_and_unknown_before_any_check(env):
    body = env["client"]().get("/api/v1/summary").json()
    assert set(body) == {"data", "meta"} and body["meta"]["storage"]["backend"] in ("sqlite", "mongodb")
    assert body["data"]["overall"]["status"] == "unknown"
    assert all(s["current"]["status"] == "unknown" for s in body["data"]["services"])
    assert body["data"]["monitoring"]["status"] == "not_started"


def test_independent_statuses_and_real_uptime(env):
    env["tick"](2)
    env["apps"].mode["novamart"] = "functional_bug"
    env["tick"](2)
    data = env["client"]().get("/api/v1/summary", params={"window": "24h"}).json()["data"]
    by_id = {s["id"]: s for s in data["services"]}
    assert by_id["novamart"]["current"]["status"] == "degraded"
    assert by_id["novaconnect"]["current"]["status"] == "operational"
    assert data["overall"] == {"status": "degraded", "label": "Partial degradation", "affected_services": ["NovaMart"]}
    up = by_id["novamart"]["uptime"]
    assert up["check_count"] == 4 and up["failed_check_count"] == 2
    assert up["uptime_pct"] == pytest.approx(50.0, abs=0.01), "computed from stored checks, not hardcoded"
    assert up["sufficient"] is False, "2 minutes of a 24 h window is insufficient history"
    assert len(by_id["novamart"]["bars"]) == 96
    assert [i["service_id"] for i in data["active_incidents"]] == ["novamart"]


def test_agent_activity_tracks_only_linked_run_and_hides_private_evidence(env):
    env["apps"].mode["novamart"] = "functional_bug"
    env["tick"]()
    c = env["client"]()
    summary = c.get("/api/v1/summary").json()["data"]
    incident_id = summary["active_incidents"][0]["id"]
    activity = summary["agent_activity"]
    assert len(activity) == 1 and activity[0]["incident_id"] == incident_id
    if not hasattr(env["storage"], "db"):
        assert activity[0]["state"] == "unavailable"
        return

    assert activity[0]["state"] == "waiting"
    db = env["storage"].db
    now = env["clock"]()
    db["incidents"].insert_one({
        "_id": "repo#unrelated", "source": "live", "live": {"uptime_incident_id": "unrelated"},
        "status": "open", "stage": "detected", "created_at": now, "updated_at": now,
    })
    assert c.get("/api/v1/summary").json()["data"]["agent_activity"][0]["state"] == "waiting"

    agent_id = f"repo#{incident_id}"
    db["incidents"].insert_one({
        "_id": agent_id, "source": "live", "live": {"uptime_incident_id": incident_id},
        "status": "open", "stage": "memory_retrieved", "created_at": now,
        "updated_at": now, "lease": {"expires_at": now + timedelta(minutes=2)},
        "last_error": "private diagnosis must stay hidden",
    })
    db["workflow_steps"].insert_one({
        "incident_id": agent_id, "stage": "memory_retrieved", "outcome": "completed",
        "at": now, "evidence": {"secret": "private diagnosis must stay hidden"},
    })
    response = c.get("/api/v1/summary")
    activity = response.json()["data"]["agent_activity"][0]
    assert activity["state"] == "working"
    assert activity["stage"] == "memory_retrieved"
    assert activity["latest_action"] == {"stage": "memory_retrieved", "outcome": "completed", "at": now.isoformat().replace("+00:00", "Z")}
    assert "private diagnosis" not in response.text

    env["clock"].advance(121)
    assert c.get("/api/v1/summary").json()["data"]["agent_activity"][0]["state"] == "waiting"

    db["incidents"].update_one({"_id": agent_id}, {"$set": {
        "status": "awaiting_merge", "pr": {"url": "https://github.com/example/repo/pull/1"},
    }})
    activity = c.get("/api/v1/summary").json()["data"]["agent_activity"][0]
    assert activity["state"] == "awaiting_review"
    assert activity["pr_url"] == "https://github.com/example/repo/pull/1"


def test_service_detail_checks_latency_and_incidents(env):
    env["apps"].mode["novaconnect"] = "backend_down"
    env["tick"](3)
    c = env["client"]()
    detail = c.get("/api/v1/services/novaconnect").json()["data"]
    assert detail["current"]["status"] == "down" and detail["current"]["failure_type"] == "backend_unreachable"
    assert set(detail["uptime"]) == {"24h", "7d", "30d"}
    assert detail["service"]["functional_path"] == "/api/monitor/probe" and detail["service"]["interval_seconds"] == 30
    assert len(detail["incident_ids"]) == 1
    checks = c.get("/api/v1/services/novaconnect/checks", params={"limit": 2}).json()["data"]
    assert len(checks) == 2 and checks[0]["checked_at"] > checks[1]["checked_at"]
    assert checks[0]["availability"]["error_kind"] == "connection_refused"
    assert len(c.get("/api/v1/services/novamart/latency").json()["data"]) == 120
    assert c.get("/api/v1/services/nope").status_code == 404


def test_incident_detail_and_agent_handoff_evidence(env):
    env["apps"].mode["novamart"] = "functional_bug"
    env["tick"]()
    c = env["client"]()
    incident = c.get("/api/v1/incidents", params={"state": "open"}).json()["data"][0]
    evidence = c.get(f"/api/v1/incidents/{incident['id']}/evidence").json()["data"]
    assert evidence["schema"] == "opsrelay.uptime_incident.v1"
    assert evidence["investigation_route"] == "investigate" and evidence["code_repair_candidate"] is True
    assert evidence["suspect_sources"] == ["backend/app/pricing.py::compute_discount"]
    assert evidence["live_check"]["healthy"] is True and evidence["live_check"]["app"] == "shopping"
    env["apps"].mode["novamart"] = "backend_down"
    env["tick"]()
    evidence = c.get(f"/api/v1/incidents/{incident['id']}/evidence").json()["data"]
    assert evidence["investigation_route"] == "service_health" and evidence["code_repair_candidate"] is False
    assert evidence["live_check"]["failure_kind"] == "connection_refused"


def test_stale_data_is_reported_as_unknown(env):
    env["tick"]()
    env["clock"].advance(600)  # worker stopped for 10 minutes
    data = env["client"]().get("/api/v1/summary").json()["data"]
    assert all(s["current"]["status"] == "unknown" and s["current"]["stale"] for s in data["services"])
    assert data["monitoring"]["status"] == "stale"


def test_mutations_need_no_token(env):
    c = env["client"]()
    r = c.patch("/api/v1/services/novamart", json={"enabled": False})
    assert r.status_code == 200 and r.json()["data"]["enabled"] is False


def test_can_attach_repair_pr_link_once(env):
    env["apps"].mode["novamart"] = "functional_bug"
    env["tick"]()
    c = env["client"]()
    inc = c.get("/api/v1/incidents").json()["data"][0]["id"]
    payload = {"kind": "repair_pr", "url": "https://github.com/faizm10/agentic-mongodb/pull/99", "label": "Fix discount"}
    for _ in range(2):
        r = c.post(f"/api/v1/incidents/{inc}/links", json=payload)
    assert r.status_code == 200 and len(r.json()["data"]["links"]) == 1


def test_demo_scenario_degraded_then_recovered_while_other_service_stays_operational(env):
    """Spec section 9: bug -> degraded within one cycle, NovaConnect unaffected, reset -> operational,
    incident history records the degraded period. Then NovaConnect's backend stops -> down (not degraded)."""
    c = env["client"]()
    status = lambda: {s["id"]: s["current"]["status"] for s in c.get("/api/v1/summary").json()["data"]["services"]}  # noqa: E731
    env["tick"]()
    assert status() == {"novamart": "operational", "novaconnect": "operational"}
    env["apps"].mode["novamart"] = "functional_bug"          # discount bug injected
    env["tick"]()                                             # the next 30 s cycle
    assert status() == {"novamart": "degraded", "novaconnect": "operational"}
    env["tick"]()
    env["apps"].mode["novamart"] = "healthy"                 # bug reset
    env["tick"]()
    assert status() == {"novamart": "operational", "novaconnect": "operational"}
    history = c.get("/api/v1/incidents", params={"service_id": "novamart"}).json()["data"]
    assert len(history) == 1 and history[0]["state"] == "resolved" and history[0]["peak_severity"] == "degraded"
    assert history[0]["duration_seconds"] == 60 and history[0]["failed_check_count"] == 2
    assert [e["type"] for e in history[0]["events"]] == ["opened", "resolved"]

    env["apps"].mode["novaconnect"] = "backend_down"         # NovaConnect backend stopped
    env["tick"]()
    assert status() == {"novamart": "operational", "novaconnect": "down"}


def test_learning_summary_is_aggregate_only(env):
    c = env["client"]()
    if not hasattr(env["storage"], "db"):
        assert c.get("/api/v1/learning").json()["data"] == {
            "available": False, "totals": {}, "strategies": [], "training": None,
            "decisions": [], "vector_indexes": []}
        return
    db = env["storage"].db
    assert c.get("/api/v1/learning").json()["data"]["available"] is False  # nothing trained yet

    now = env["clock"]()
    db["rl_experiences"].insert_many([
        {"app": "shopping", "action": "fix_discount_divisor", "reward": 1.0, "source": "mock",
         "state_text": "private log line", "embedding": [0.1] * 4},
        {"app": "shopping", "action": "fix_discount_divisor", "reward": 1.0, "source": "ci", "embedding": [0.1] * 4},
        {"app": "shopping", "action": "revert_last_commit", "reward": 0.0, "source": "mock", "embedding": [0.1] * 4},
    ])
    db["mock_incidents"].insert_one({"_id": "mock:000", "failed_logs": "private log line"})
    db["rl_training_runs"].insert_one({
        "at": now, "accuracy_before": 0.13, "accuracy_after": 0.83, "per_bug": {"discount": 1.0},
        "curve": [{"epoch": 1, "incidents": "1-25", "first_try_rate": 0.24, "avg_attempts": 2.3}]})
    db["incidents"].insert_one({
        "_id": "repo#9", "app": "shopping", "source": "ci", "status": "awaiting_merge", "created_at": now,
        "rl": {"action": "fix_discount_divisor", "expected_success": 0.95, "hinted": True},
        "pr": {"url": "javascript:alert(1)"}, "failed_logs": "private log line"})

    r = c.get("/api/v1/learning")
    data = r.json()["data"]
    assert data["available"] and data["totals"]["experiences"] == 3 and data["totals"]["real_experiences"] == 1
    assert data["totals"]["mock_incidents"] == 1
    top = data["strategies"][0]
    assert (top["action"], top["tries"], top["success_rate"], top["real_tries"]) == ("fix_discount_divisor", 2, 1.0, 1)
    assert data["training"]["accuracy_after"] == 0.83 and data["training"]["curve"][0]["first_try_rate"] == 0.24
    assert data["decisions"][0]["action"] == "fix_discount_divisor" and data["decisions"][0]["pr_url"] is None
    assert "private log line" not in r.text and "\"embedding\":" not in r.text


def test_accounts_only_see_their_own_workspaces(env):
    env["apps"].mode["novamart"] = "functional_bug"
    env["tick"](2)
    owner = env["client"]()  # first sign-up: owns the demo workspace and its services
    incident_id = owner.get("/api/v1/incidents").json()["data"][0]["id"]
    other = env["client"]("other@example.com")

    assert other.get("/api/v1/auth/me").json()["data"]["workspaces"] == []
    assert other.get("/api/v1/summary").json()["error"]["message"] == "Create a workspace first."
    own_ws = other.post("/api/v1/workspaces", json={"name": "Other team"}).json()["data"]["id"]
    data = other.get("/api/v1/summary").json()["data"]
    assert data["services"] == [] and data["active_incidents"] == []
    assert other.get("/api/v1/incidents").json()["data"] == []

    # Guessing the other account's workspace or incident ids finds nothing.
    for path in ("/api/v1/summary", "/api/v1/services", "/api/v1/incidents"):
        assert other.get(path, headers={"x-opsrelay-workspace": "demo"}).status_code == 404
    other.cookies.set("opsrelay_workspace", "demo")
    assert other.get("/api/v1/summary").status_code == 404
    other.cookies.set("opsrelay_workspace", own_ws)
    for path in (f"/api/v1/incidents/{incident_id}", f"/api/v1/incidents/{incident_id}/evidence",
                 f"/api/v1/incidents/{incident_id}/walkthrough", "/api/v1/services/novamart",
                 "/api/v1/services/novamart/checks"):
        assert other.get(path).status_code == 404, path
    assert other.post(f"/api/v1/incidents/{incident_id}/acknowledge", json={"by": "x"}).status_code == 404
    assert other.patch("/api/v1/services/novamart", json={"enabled": False}).status_code == 404
    assert owner.get("/api/v1/summary", headers={"x-opsrelay-workspace": own_ws}).status_code == 404
    assert owner.get(f"/api/v1/incidents/{incident_id}").status_code == 200

    assert TestClient(owner.app).get("/api/v1/summary").status_code == 401
    assert other.post("/api/v1/auth/sign-in", json={"email": "other@example.com", "password": "wrong pass"}).status_code == 401
    other.post("/api/v1/auth/sign-out")
    assert other.get("/api/v1/auth/me").status_code == 401
