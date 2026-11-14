"""Rule 4 (persistence), realtime fan-out, and the monitoring endpoints."""

import pytest
from fastapi.testclient import TestClient

from app import messages, monitor
from app.main import create_app

from ._assertions import expect_equal, expect_status
from .conftest import FakeClock, as_user

PERSIST = pytest.mark.opsrelay(component="persistence", rule="Rule 4 - persistence")
REALTIME = pytest.mark.opsrelay(component="realtime", rule="realtime delivery")
MONITOR = pytest.mark.opsrelay(component="monitoring", rule="health and probe")


# ------------------------------------------------------------------ persistence
@PERSIST
def test_messages_survive_a_backend_restart(db_path):
    with TestClient(create_app(db_path, clock=FakeClock())) as first:
        sent = first.post("/api/messages", headers=as_user("u_alex"),
                          json={"conversation_id": "c_alex_sarah", "body": "Persist me", "idempotency_key": "p-1"}).json()
        first.post(f"/api/messages/{sent['id']}/reactions", headers=as_user("u_sarah"), json={"emoji": "💾"})
        total = first.app.state.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        first.app.state.conn.close()

    with TestClient(create_app(db_path, clock=FakeClock())) as second:  # simulated restart
        history = second.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_sarah")).json()["messages"]
        restored = next((m for m in history if m["id"] == sent["id"]), None)
        expect_equal(
            (restored or {}).get("body"), "Persist me", rule="Rule 4 - persistence",
            what="message must still exist after restart", function="app.db / SQLite",
        )
        assert restored["reactions"][0]["emoji"] == "💾"
        assert second.app.state.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == total, \
            "seed data must not be inserted twice"
        replay = second.post("/api/messages", headers=as_user("u_alex"),
                             json={"conversation_id": "c_alex_sarah", "body": "Persist me", "idempotency_key": "p-1"})
        assert replay.status_code == 200 and replay.json()["id"] == sent["id"], "idempotency survives restarts"


# --------------------------------------------------------------------- realtime
@REALTIME
def test_two_users_receive_each_others_messages_live(client):
    with client.websocket_connect("/ws/u_alex") as alex, client.websocket_connect("/ws/u_sarah") as sarah:
        assert alex.receive_json()["type"] == "hello"
        hello = sarah.receive_json()
        while hello["type"] != "hello":
            hello = sarah.receive_json()
        assert "u_alex" in hello["online_user_ids"]
        sent = client.post("/api/messages", headers=as_user("u_alex"),
                           json={"conversation_id": "c_alex_sarah", "body": "Live from Alex"}).json()
        event = sarah.receive_json()
        while event["type"] != "message.created":
            event = sarah.receive_json()
        assert event["message"]["id"] == sent["id"] and event["message"]["body"] == "Live from Alex"
        assert sent["status"] == "delivered", "Sarah was connected, so delivery is confirmed"

        sarah.send_json({"type": "typing", "conversation_id": "c_alex_sarah", "is_typing": True})
        event = alex.receive_json()
        while event["type"] != "typing":
            event = alex.receive_json()
        assert event == {"type": "typing", "conversation_id": "c_alex_sarah", "user_id": "u_sarah", "is_typing": True}

        sarah.send_json({"type": "ping", "ts": 1})
        event = sarah.receive_json()
        while event["type"] != "pong":
            event = sarah.receive_json()


@REALTIME
def test_unknown_user_websocket_is_rejected(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect("/ws/u_ghost") as ws:
            ws.receive_json()
    assert info.value.code == 4404


# ------------------------------------------------------------------- monitoring
@MONITOR
def test_health_reports_availability(client):
    body = expect_status(client.get("/health"), 200, what="availability check")
    assert body["service"] == "messaging" and body["check"] == "availability" and body["status"] == "ok"


@MONITOR
def test_functional_probe_is_healthy_on_clean_code(client):
    for method in (client.get, client.post):
        body = expect_status(method("/api/monitor/probe"), 200, what="probe must pass on clean code")
        assert body["service"] == "messaging" and body["status"] == "healthy", body["failed_checks"]
        assert [c["check"] for c in body["checks"]] == [
            "database_reachable", "send_and_receive", "idempotent_retry", "delivery_status",
            "message_ordering", "conversation_isolation",
        ]
        assert all({"service", "check", "status", "timestamp", "failure_reason", "details"} <= set(c)
                   for c in body["checks"])


@MONITOR
def test_probe_does_not_touch_persistent_data(client):
    conn = client.app.state.conn
    before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    for _ in range(3):
        client.post("/api/monitor/probe")
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == before
    assert client.post("/api/monitor/probe").json()["side_effects"] == {"persistent_messages_written": 0}


@MONITOR
def test_probe_reports_structured_failure(client, monkeypatch):
    monkeypatch.setattr(messages, "sort_timeline", lambda items: list(reversed(sorted(items, key=lambda m: m["seq"]))))
    body = expect_status(client.get("/api/monitor/probe"), 503, what="broken ordering must make the probe fail")
    failed = {c["check"]: c for c in body["checks"] if c["status"] == "fail"}
    assert "message_ordering" in failed
    assert failed["message_ordering"]["failure_reason"].startswith("Rule 3 violated")
    assert failed["message_ordering"]["details"]["expected"] == ["probe: one", "probe: two", "probe: three"]


@MONITOR
def test_probe_cli_exit_code(db_path, monkeypatch, capsys):
    monkeypatch.setenv("NOVACONNECT_DB_PATH", db_path)
    assert monitor.main(["--init-db"]) == 0
    assert '"status": "healthy"' in capsys.readouterr().out
