"""Sending and receiving failures must affect the app and its monitor."""

import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from .conftest import as_user

pytestmark = pytest.mark.opsrelay(component="demo", rule="outage controls")


@pytest.mark.parametrize("scenario", ["send", "receive", "both"])
def test_demo_outage_and_restore(client, db_path, scenario):
    fault = Path(db_path).parent / "opsrelay-outage.json"
    assert client.get("/api/monitor/probe").status_code == 200
    fault.write_text(json.dumps({"scenario": scenario}))

    assert client.get("/health").status_code == 200
    assert client.get("/api/monitor/probe").status_code == 503
    send = client.post("/api/messages", headers=as_user("u_alex"),
                       json={"conversation_id": "c_alex_sarah", "body": "demo"})
    history = client.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_sarah"))
    assert send.status_code == (503 if scenario in {"send", "both"} else 201)
    assert history.status_code == (503 if scenario in {"receive", "both"} else 200)
    if scenario in {"receive", "both"}:
        assert client.get("/api/conversations", headers=as_user("u_sarah")).status_code == 503
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/u_sarah"):
                pass

    fault.unlink()
    assert client.get("/api/monitor/probe").status_code == 200
    assert client.post("/api/messages", headers=as_user("u_alex"),
                       json={"conversation_id": "c_alex_sarah", "body": "restored"}).status_code == 201
    assert client.get("/api/conversations/c_alex_sarah/messages", headers=as_user("u_sarah")).status_code == 200


def test_receive_outage_stops_delivery_to_existing_socket(client, db_path):
    fault = Path(db_path).parent / "opsrelay-outage.json"
    with client.websocket_connect("/ws/u_sarah") as socket:
        assert socket.receive_json()["type"] == "hello"
        fault.write_text(json.dumps({"scenario": "receive"}))
        try:
            sent = client.post("/api/messages", headers=as_user("u_alex"),
                               json={"conversation_id": "c_alex_sarah", "body": "not delivered"})
            assert sent.status_code == 201
            assert sent.json()["status"] == "sent"
        finally:
            fault.unlink()
